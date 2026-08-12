from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import PurePath
from typing import Any
from zoneinfo import ZoneInfo

from lkp.db import SessionLocal
from lkp.models import (
    ChunkEmbedding,
    DeveloperFeedPost,
    Document,
    DocumentChunk,
    DocumentState,
    DocumentVersion,
    ProjectJournalEntry,
)
from lkp.redaction import redact_text, redact_value
from lkp.security_boundary import allowed_project_expression
from lkp.settings import Settings, get_settings
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .generation import DeveloperFeedDraft, GenerationProvider, OllamaGenerationProvider
from .service_runtime import assert_mount_guards, service_pid

_LOCK_KEY = "developer_feed.publisher.v2"
_FEED_JOURNAL_STATUSES = ("VERIFIED", "OBSERVED_CHANGE")


@dataclass(frozen=True, slots=True)
class FeedBatch:
    project: str
    journals: list[ProjectJournalEntry]
    documents: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    embedded_from: datetime
    embedded_to: datetime


@dataclass(frozen=True, slots=True)
class FeedHistory:
    journal_ids: frozenset[str]
    content_hashes: frozenset[str]
    topics: tuple[dict[str, str], ...]
    thread_texts: tuple[str, ...]


def _basename(value: str) -> str:
    return PurePath(value.replace("\\", "/")).name


def _journal_document_filename(journal: ProjectJournalEntry) -> str:
    return (
        f"{journal.occurred_at.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-"
        f"{str(journal.id)[:8]}.md"
    )


def _embedded_documents(
    session: Session,
    journal: ProjectJournalEntry,
    settings: Settings,
) -> list[dict[str, Any]]:
    changed_filenames = {
        _basename(path).casefold() for path in journal.changed_files if _basename(path)
    }
    journal_filename = _journal_document_filename(journal).casefold()
    filenames = sorted({journal_filename, *changed_filenames})
    rows = session.execute(
        select(
            Document.id,
            DocumentVersion.id,
            Document.filename,
            Document.project_relative_path,
            DocumentVersion.content_hash,
            func.max(ChunkEmbedding.created_at),
        )
        .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
        .join(DocumentChunk, DocumentChunk.document_version_id == DocumentVersion.id)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
        .where(
            Document.project_key == journal.project_key,
            Document.state == DocumentState.active,
            func.lower(Document.filename).in_(filenames),
            ChunkEmbedding.embedding_revision == settings.embedding_revision,
        )
        .group_by(
            Document.id,
            DocumentVersion.id,
            Document.filename,
            Document.project_relative_path,
            DocumentVersion.content_hash,
        )
    ).all()
    return [
        {
            "document_id": str(document_id),
            "document_version_id": str(version_id),
            "filename": filename,
            "project_relative_path": relative_path,
            "content_hash": content_hash,
            "embedded_at": embedded_at,
        }
        for (
            document_id,
            version_id,
            filename,
            relative_path,
            content_hash,
            embedded_at,
        ) in rows
    ]


def _recent_feed_history(
    session: Session,
    project: str,
    *,
    limit: int,
    post_type: str = "activity",
) -> FeedHistory:
    roots = list(
        session.scalars(
            select(DeveloperFeedPost)
            .where(
                DeveloperFeedPost.project_key == project,
                DeveloperFeedPost.post_type == post_type,
                DeveloperFeedPost.sequence == 0,
                allowed_project_expression(DeveloperFeedPost.project_key),
            )
            .order_by(DeveloperFeedPost.created_at.desc(), DeveloperFeedPost.id.desc())
            .limit(limit)
        )
    )
    root_ids = [root.thread_root_id or root.id for root in roots]
    posts = (
        list(
            session.scalars(
                select(DeveloperFeedPost)
                .where(
                    DeveloperFeedPost.thread_root_id.in_(root_ids),
                    DeveloperFeedPost.project_key == project,
                    allowed_project_expression(DeveloperFeedPost.project_key),
                )
                .order_by(DeveloperFeedPost.thread_root_id, DeveloperFeedPost.sequence)
            )
        )
        if root_ids
        else []
    )
    posts_by_root: dict[str, list[DeveloperFeedPost]] = {}
    for post in posts:
        posts_by_root.setdefault(str(post.thread_root_id or post.id), []).append(post)

    journal_ids: set[str] = set()
    content_hashes: set[str] = set()
    topics: list[dict[str, str]] = []
    thread_texts: list[str] = []
    for root in roots:
        manifest = root.source_manifest_json or {}
        journal_ids.update(str(value) for value in manifest.get("journal_ids", []))
        content_hashes.update(
            str(document.get("content_hash"))
            for document in manifest.get("documents", [])
            if document.get("content_hash")
        )
        contract = manifest.get("editorial_contract") or {}
        technology = str(contract.get("technology_or_method") or "").strip()
        problem = str(contract.get("reader_problem_or_goal") or "").strip()
        if technology or problem:
            topics.append(
                {
                    "technology_or_method": technology,
                    "reader_problem_or_goal": problem,
                }
            )
        root_posts = posts_by_root.get(str(root.thread_root_id or root.id), [])
        if root_posts:
            thread_texts.append(
                "\n".join(
                    f"{post.content_ko}\n{post.content_en}"
                    for post in sorted(root_posts, key=lambda item: item.sequence)
                )
            )
    return FeedHistory(
        journal_ids=frozenset(journal_ids),
        content_hashes=frozenset(content_hashes),
        topics=tuple(topics),
        thread_texts=tuple(thread_texts),
    )


def _document_sources(
    session: Session,
    documents: list[dict[str, Any]],
    *,
    start_index: int,
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for document_offset, document in enumerate(documents):
        chunks = session.execute(
            select(
                DocumentChunk.chunk_index,
                DocumentChunk.heading_path,
                DocumentChunk.symbol_name,
                DocumentChunk.start_line,
                DocumentChunk.end_line,
                DocumentChunk.content,
            )
            .where(
                DocumentChunk.document_version_id
                == uuid.UUID(str(document["document_version_id"]))
            )
            .order_by(DocumentChunk.chunk_index)
            .limit(3)
        ).all()
        excerpts = [
            {
                "heading": heading or symbol,
                "lines": f"{start_line}-{end_line}",
                "content": redact_text(content, max_chars=2400),
            }
            for _, heading, symbol, start_line, end_line, content in chunks
        ]
        if excerpts:
            sources.append(
                {
                    "id": f"D{start_index + document_offset}",
                    "source_type": "current_embedded_document",
                    "filename": document["filename"],
                    "content_hash": document["content_hash"],
                    "embedded_at": document["embedded_at"].isoformat(),
                    "excerpts": excerpts,
                }
            )
    return sources


def _eligible_batch(
    session: Session,
    settings: Settings,
    *,
    now: datetime,
    excluded_projects: set[str] | None = None,
) -> FeedBatch | None:
    excluded_projects = excluded_projects or set()
    initial_cursor = now - timedelta(hours=settings.developer_feed_initial_lookback_hours)
    project_cursors = {
        str(project): embedded_to
        for project, embedded_to in session.execute(
            select(
                DeveloperFeedPost.project_key,
                func.max(DeveloperFeedPost.source_embedding_to),
            )
            .where(
                DeveloperFeedPost.post_type == "activity",
                DeveloperFeedPost.sequence == 0,
                DeveloperFeedPost.project_key != "workstation",
                allowed_project_expression(DeveloperFeedPost.project_key),
            )
            .group_by(DeveloperFeedPost.project_key)
        ).all()
        if embedded_to is not None
    }
    journals = list(
        session.scalars(
            select(ProjectJournalEntry)
            .where(
                ProjectJournalEntry.verification_status.in_(_FEED_JOURNAL_STATUSES),
                allowed_project_expression(ProjectJournalEntry.project_key),
                ProjectJournalEntry.occurred_at
                >= now - timedelta(hours=settings.developer_feed_initial_lookback_hours),
            )
            .order_by(ProjectJournalEntry.occurred_at, ProjectJournalEntry.id)
            .limit(1000)
        )
    )
    histories: dict[str, FeedHistory] = {}
    eligible_by_project: dict[
        str, list[tuple[ProjectJournalEntry, list[dict[str, Any]]]]
    ] = {}
    for journal in journals:
        if journal.project_key in excluded_projects:
            continue
        history = histories.get(journal.project_key)
        if history is None:
            history = _recent_feed_history(
                session,
                journal.project_key,
                limit=settings.developer_feed_recent_thread_limit,
            )
            histories[journal.project_key] = history
        if str(journal.id) in history.journal_ids:
            continue
        documents = [
            document
            for document in _embedded_documents(session, journal, settings)
            if str(document["content_hash"]) not in history.content_hashes
        ]
        cursor = project_cursors.get(journal.project_key, initial_cursor)
        if documents and max(item["embedded_at"] for item in documents) > cursor:
            eligible_by_project.setdefault(journal.project_key, []).append(
                (journal, documents)
            )
    if not eligible_by_project:
        return None

    project, eligible = min(
        eligible_by_project.items(),
        key=lambda item: min(
            max(document["embedded_at"] for document in documents)
            for _, documents in item[1]
        ),
    )
    eligible.sort(
        key=lambda item: (
            max(document["embedded_at"] for document in item[1]),
            item[0].occurred_at,
            str(item[0].id),
        )
    )
    eligible = eligible[: settings.developer_feed_max_sources_per_run]
    selected_journals = [item[0] for item in eligible]
    documents_by_id: dict[str, dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []
    for index, (journal, documents) in enumerate(eligible, start=1):
        passing_checks = [
            redact_value(item)
            for item in (journal.verification_json or [])
            if item.get("exit_code") == 0
        ]
        sources.append(
            {
                "id": f"J{index}",
                "source_type": "verified_project_journal",
                "project": journal.project_key,
                "occurred_at": journal.occurred_at.isoformat(),
                "title": redact_text(journal.title, max_chars=500),
                "intent": redact_text(journal.intent, max_chars=1500),
                "change": redact_text(journal.change_summary, max_chars=3000),
                "resolution": redact_text(journal.resolution, max_chars=1500),
                "verification_status": journal.verification_status,
                "claim_scope": (
                    "verified_result"
                    if journal.verification_status == "VERIFIED"
                    else "observed_change_only"
                ),
                "passing_verification": passing_checks[:10],
            }
        )
        for document in documents:
            documents_by_id[str(document["document_id"])] = document
    selected_documents = sorted(
        documents_by_id.values(),
        key=lambda item: (str(item["filename"]), str(item["document_id"])),
    )
    sources.extend(
        _document_sources(session, selected_documents, start_index=len(sources) + 1)
    )
    embedded_times = [item["embedded_at"] for item in selected_documents]
    return FeedBatch(
        project=project,
        journals=selected_journals,
        documents=selected_documents,
        sources=sources,
        embedded_from=min(embedded_times),
        embedded_to=max(embedded_times),
    )


def inspect_due(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    batch = _eligible_batch(session, settings, now=now)
    local_now = now.astimezone(ZoneInfo(settings.developer_feed_timezone))
    summary_date = local_now.date() - timedelta(
        days=settings.developer_feed_daily_summary_lag_days
    )
    daily_key = f"daily:{summary_date.isoformat()}:0"
    daily_exists = bool(
        session.scalar(
            select(DeveloperFeedPost.id).where(
                DeveloperFeedPost.publication_key == daily_key
            )
        )
    )
    return {
        "pending_sources": len(batch.journals) if batch else 0,
        "pending_documents": len(batch.documents) if batch else 0,
        "daily_due": local_now.hour >= settings.developer_feed_daily_hour
        and not daily_exists,
    }


def _daily_summary_window(
    settings: Settings,
    *,
    now: datetime,
) -> tuple[date, datetime, datetime]:
    local_now = now.astimezone(ZoneInfo(settings.developer_feed_timezone))
    summary_date = local_now.date() - timedelta(
        days=settings.developer_feed_daily_summary_lag_days
    )
    start_local = datetime.combine(
        summary_date,
        datetime.min.time(),
        tzinfo=local_now.tzinfo,
    )
    end_local = start_local + timedelta(days=1)
    return (
        summary_date,
        start_local.astimezone(timezone.utc),
        min(end_local.astimezone(timezone.utc), now),
    )


def _add_thread(
    session: Session,
    *,
    key: str,
    project: str,
    post_type: str,
    draft: DeveloperFeedDraft,
    manifest: dict[str, Any],
    embedded_from: datetime | None,
    embedded_to: datetime | None,
    settings: Settings,
    created_at: datetime,
) -> list[DeveloperFeedPost]:
    posts: list[DeveloperFeedPost] = []
    root_id = uuid.uuid4()
    previous_id: uuid.UUID | None = None
    for sequence, message in enumerate(draft.posts):
        post_id = root_id if sequence == 0 else uuid.uuid4()
        post = DeveloperFeedPost(
            id=post_id,
            publication_key=f"{key}:{sequence}",
            project_key=project,
            post_type=post_type,
            thread_root_id=root_id,
            reply_to_id=previous_id,
            sequence=sequence,
            content_ko=message.content_ko.strip(),
            content_en=message.content_en.strip(),
            source_manifest_json={
                **manifest,
                "editorial_contract": {
                    "publication_kind": draft.publication_kind,
                    "technology_or_method": draft.technology_or_method,
                    "reader_problem_or_goal": draft.reader_problem_or_goal,
                    "outcome_status": draft.outcome_status,
                    "outcome_source_ids": draft.outcome_source_ids,
                },
                "post_source_ids": message.source_ids,
                "editorial_role": message.role,
                "claim_mode": (
                    "verified_outcome"
                    if message.role == "possibility"
                    and draft.outcome_status != "not_measured"
                    else "unmeasured_outcome"
                    if message.role == "possibility"
                    else "reproduction_boundary"
                    if message.role == "afterthought"
                    else "reproducible_method"
                    if message.role == "meaning"
                    else "evidence_bound"
                ),
            },
            source_embedding_from=embedded_from,
            source_embedding_to=embedded_to,
            persona_version=settings.developer_feed_persona_version,
            created_at=created_at,
        )
        session.add(post)
        posts.append(post)
        previous_id = post_id
    return posts


def _provider(
    settings: Settings,
    *,
    keep_alive: str = "0",
) -> OllamaGenerationProvider:
    return OllamaGenerationProvider(
        settings.generation_base_url,
        settings.developer_feed_model,
        settings.developer_feed_model_digest,
        settings.developer_feed_timeout_seconds,
        temperature=settings.developer_feed_temperature,
        context_window=settings.developer_feed_context_window,
        num_batch=settings.developer_feed_num_batch,
        max_output_tokens=settings.developer_feed_max_output_tokens,
        keep_alive=keep_alive,
    )


def _bounded_payload(payload: dict[str, Any], max_chars: int) -> dict[str, Any]:
    def size() -> int:
        return len(json.dumps(payload, ensure_ascii=False))

    while size() > max_chars:
        documents = [
            source
            for source in payload["sources"]
            if source.get("source_type") == "current_embedded_document"
        ]
        if documents:
            payload["sources"].remove(documents[-1])
            continue
        break
    protected_keys = {
        "id",
        "source_type",
        "project",
        "filename",
        "content_hash",
        "occurred_at",
        "embedded_at",
        "lines",
    }

    def mutable_strings(
        value: Any,
    ) -> list[tuple[int, dict | list, str | int]]:
        candidates: list[tuple[int, dict | list, str | int]] = []
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str):
                    if key not in protected_keys and len(item) > 64:
                        candidates.append((len(item), value, key))
                else:
                    candidates.extend(mutable_strings(item))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, str):
                    if len(item) > 64:
                        candidates.append((len(item), value, index))
                else:
                    candidates.extend(mutable_strings(item))
        return candidates

    while size() > max_chars:
        candidates = mutable_strings(payload)
        if not candidates:
            raise ValueError(
                "developer feed payload cannot fit the configured character budget "
                "without dropping source identity"
            )
        length, container, key = max(candidates, key=lambda item: item[0])
        overflow = size() - max_chars
        target = max(64, length - max(64, min(length - 64, overflow + 8)))
        original = str(container[key])
        container[key] = f"{original[: target - 1].rstrip()}…"
    return payload


def _exact_phrase_candidates(sources: list[dict[str, Any]]) -> dict[str, list[str]]:
    technology: list[str] = []
    problems: list[str] = []

    def add(target: list[str], value: Any, *, limit: int) -> None:
        candidate = re.sub(r"\s+", " ", str(value or "")).strip()
        if 3 <= len(candidate) <= limit and candidate not in target:
            target.append(candidate)

    for source in sources:
        add(technology, source.get("title"), limit=120)
        add(technology, source.get("filename"), limit=120)
        add(problems, source.get("title"), limit=160)
        add(problems, source.get("intent"), limit=180)
        for check in source.get("passing_verification", []):
            if isinstance(check, dict):
                add(technology, check.get("command_family"), limit=120)
        for excerpt in source.get("excerpts", []):
            if isinstance(excerpt, dict):
                add(technology, excerpt.get("heading"), limit=120)
    return {
        "technology_or_method": technology[:24],
        "reader_problem_or_goal": problems[:24],
    }


def _normalized_similarity_text(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", value.casefold())


def _sequence_similarity(left: str, right: str) -> float:
    normalized_left = _normalized_similarity_text(left)
    normalized_right = _normalized_similarity_text(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def _ngram_similarity(left: str, right: str, *, width: int = 3) -> float:
    def grams(value: str) -> set[str]:
        normalized = _normalized_similarity_text(value)
        if len(normalized) < width:
            return {normalized} if normalized else set()
        return {
            normalized[index : index + width]
            for index in range(len(normalized) - width + 1)
        }

    left_grams = grams(left)
    right_grams = grams(right)
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def _draft_thread_text(draft: DeveloperFeedDraft) -> str:
    return "\n".join(
        f"{post.content_ko}\n{post.content_en}" for post in draft.posts
    )


def _duplicate_draft_reason(
    draft: DeveloperFeedDraft,
    history: FeedHistory,
    settings: Settings,
) -> str | None:
    for topic in history.topics:
        technology_score = _sequence_similarity(
            draft.technology_or_method,
            topic.get("technology_or_method", ""),
        )
        problem_score = _sequence_similarity(
            draft.reader_problem_or_goal,
            topic.get("reader_problem_or_goal", ""),
        )
        topic_score = (technology_score + problem_score) / 2
        if topic_score >= settings.developer_feed_topic_similarity_threshold:
            return f"topic_similarity:{topic_score:.3f}"
    draft_text = _draft_thread_text(draft)
    for prior_text in history.thread_texts:
        text_score = _ngram_similarity(draft_text, prior_text)
        if text_score >= settings.developer_feed_text_similarity_threshold:
            return f"text_similarity:{text_score:.3f}"
    return None


def _generate_novel_draft(
    generator: GenerationProvider,
    payload: dict[str, Any],
    *,
    history: FeedHistory,
    settings: Settings,
) -> tuple[DeveloperFeedDraft | None, str, str | None]:
    guarded_payload = {
        **payload,
        "recent_topics_to_avoid": list(history.topics),
        "novelty_contract": (
            "Choose a materially different problem and method from recent topics. "
            "New wording for the same lesson is not novel. Use only the supplied new evidence."
        ),
    }
    draft, digest = generator.write_developer_feed(
        guarded_payload,
        prompt_version=settings.developer_feed_prompt_version,
    )
    reason = _duplicate_draft_reason(draft, history, settings)
    if reason is None:
        return draft, digest, None

    repair_payload = {
        **guarded_payload,
        "novelty_repair": (
            f"The previous candidate failed deterministic novelty validation ({reason}). "
            "Select another supported problem/method from the sources; do not paraphrase "
            "the rejected topic."
        ),
    }
    repaired, repaired_digest = generator.write_developer_feed(
        repair_payload,
        prompt_version=settings.developer_feed_prompt_version,
    )
    repaired_reason = _duplicate_draft_reason(repaired, history, settings)
    if repaired_reason is not None:
        return None, repaired_digest, repaired_reason
    return repaired, repaired_digest, None


def publish_once(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    provider: GenerationProvider | None = None,
    excluded_projects: set[str] | None = None,
    skip_daily: bool = False,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if not settings.developer_feed_enabled:
        return {"state": "disabled", "activity_threads": 0, "daily_threads": 0}
    acquired = session.execute(
        text("select pg_try_advisory_xact_lock(hashtext(:key))"),
        {"key": _LOCK_KEY},
    ).scalar_one()
    if not acquired:
        return {"state": "standby_lock_held", "activity_threads": 0, "daily_threads": 0}

    batch = _eligible_batch(
        session,
        settings,
        now=now,
        excluded_projects=excluded_projects,
    )
    local_now = now.astimezone(ZoneInfo(settings.developer_feed_timezone))
    summary_date, day_start, day_end = _daily_summary_window(settings, now=now)
    local_date = summary_date.isoformat()
    daily_key = f"daily:{local_date}:0"
    daily_due = (
        not skip_daily
        and local_now.hour >= settings.developer_feed_daily_hour
        and not session.scalar(
            select(DeveloperFeedPost.id).where(
                DeveloperFeedPost.publication_key == daily_key
            )
        )
    )
    if batch is None and not daily_due:
        return {
            "state": "idle",
            "activity_threads": 0,
            "daily_threads": 0,
            "posts": 0,
        }

    generator = provider or _provider(settings)
    created: list[DeveloperFeedPost] = []
    activity_threads = 0
    daily_threads = 0
    generated_manifests: list[dict[str, Any]] = []
    novelty_rejections: list[dict[str, str]] = []
    content_rejections: list[dict[str, str]] = []

    if batch is not None:
        payload = _bounded_payload(
            {
                "post_type": "information_update",
                "editorial_intent": (
                    "Publish a searchable, reproducible technical post rather than a work log. "
                    "Name the technology or method and the problem it solves, give the actual "
                    "settings, commands, component order, or diagnostic steps, report the verified "
                    "effect, verified lack of effect, trade-off, or unmeasured status honestly, "
                    "and close with the environment or failure boundary."
                    " Sources marked observed_change_only prove only that the current embedded "
                    "change exists; do not present their claimed success, effect, or metric as "
                    "verified."
                ),
                "window": {
                    "embedded_from": batch.embedded_from.isoformat(),
                    "embedded_to": batch.embedded_to.isoformat(),
                },
                "sources": list(batch.sources),
            },
            settings.developer_feed_max_input_chars,
        )
        phrase_candidates = _exact_phrase_candidates(payload["sources"])
        payload["exact_phrase_candidates"] = phrase_candidates
        while (
            len(json.dumps(payload, ensure_ascii=False))
            > settings.developer_feed_max_input_chars
        ):
            target = max(phrase_candidates.values(), key=len)
            if not target:
                payload.pop("exact_phrase_candidates", None)
                if (
                    len(json.dumps(payload, ensure_ascii=False))
                    > settings.developer_feed_max_input_chars
                ):
                    raise ValueError(
                        "developer feed phrase catalog cannot fit input budget"
                    )
                break
            target.pop()
        history = _recent_feed_history(
            session,
            batch.project,
            limit=settings.developer_feed_recent_thread_limit,
        )
        try:
            draft, digest, duplicate_reason = _generate_novel_draft(
                generator,
                payload,
                history=history,
                settings=settings,
            )
        except ValueError:
            draft = None
            digest = "none"
            duplicate_reason = None
            content_rejections.append(
                {
                    "post_type": "activity",
                    "project": batch.project,
                    "reason": "deterministic_content_gate_rejected",
                }
            )
        if draft is None:
            if duplicate_reason:
                novelty_rejections.append(
                    {
                        "post_type": "activity",
                        "project": batch.project,
                        "reason": duplicate_reason,
                    }
                )
        else:
            batch_hash = hashlib.sha256(
                "|".join(str(journal.id) for journal in batch.journals).encode()
            ).hexdigest()[:20]
            manifest = {
                "journal_ids": [str(journal.id) for journal in batch.journals],
                "documents": [
                    {
                        **document,
                        "embedded_at": document["embedded_at"].isoformat(),
                    }
                    for document in batch.documents
                ],
                "source_catalog": payload["sources"],
                "embedding_revision": settings.embedding_revision,
                "generator": {
                    "provider": generator.provider,
                    "model": generator.model,
                    "model_digest": digest,
                    "prompt_version": settings.developer_feed_prompt_version,
                },
                "screenshot_recommendation": (
                    {
                        "source_id": draft.screenshot_source_id,
                        "reason": draft.screenshot_reason,
                        "state": "recommended_not_captured",
                    }
                    if draft.screenshot_source_id
                    else None
                ),
            }
            created.extend(
                _add_thread(
                    session,
                    key=f"activity:{batch_hash}",
                    project=batch.project,
                    post_type="activity",
                    draft=draft,
                    manifest=manifest,
                    embedded_from=batch.embedded_from,
                    embedded_to=batch.embedded_to,
                    settings=settings,
                    created_at=now,
                )
            )
            generated_manifests.append(manifest)
            activity_threads = 1

    if daily_due:
        activity_roots = list(
            session.scalars(
                select(DeveloperFeedPost).where(
                    DeveloperFeedPost.post_type == "activity",
                    allowed_project_expression(DeveloperFeedPost.project_key),
                    DeveloperFeedPost.sequence == 0,
                    DeveloperFeedPost.created_at >= day_start,
                    DeveloperFeedPost.created_at < day_end,
                )
            )
        )
        source_catalog: list[dict[str, Any]] = []
        for post in activity_roots:
            source_catalog.extend(
                (post.source_manifest_json or {}).get("source_catalog", [])
            )
        if summary_date == local_now.date():
            for manifest in generated_manifests:
                source_catalog.extend(manifest.get("source_catalog", []))
        unique_sources = {
            (
                source.get("source_type"),
                source.get("content_hash")
                or source.get("occurred_at")
                or source.get("id"),
            ): source
            for source in source_catalog
        }
        sources = [
            {**source, "id": f"S{index}"}
            for index, source in enumerate(unique_sources.values(), start=1)
        ]
        if sources:
            payload = _bounded_payload(
                {
                    "post_type": "daily_summary",
                    "local_date": local_date,
                    "editorial_intent": (
                        "Choose the day's most reproducible technical lesson for other developers. "
                        "Name the method and searchable problem, explain exact steps or settings, "
                        "state the measured effect, lack of effect, trade-off, or unmeasured "
                        "status, "
                        "and give the environment boundary. Do not narrate the day, list completed "
                        "work, or call this a daily summary."
                    ),
                    "sources": sources,
                },
                settings.developer_feed_max_input_chars,
            )
            phrase_candidates = _exact_phrase_candidates(payload["sources"])
            payload["exact_phrase_candidates"] = phrase_candidates
            while (
                len(json.dumps(payload, ensure_ascii=False))
                > settings.developer_feed_max_input_chars
            ):
                target = max(phrase_candidates.values(), key=len)
                if not target:
                    payload.pop("exact_phrase_candidates", None)
                    if (
                        len(json.dumps(payload, ensure_ascii=False))
                        > settings.developer_feed_max_input_chars
                    ):
                        raise ValueError(
                            "developer feed phrase catalog cannot fit input budget"
                        )
                    break
                target.pop()
            daily_history = _recent_feed_history(
                session,
                "workstation",
                limit=settings.developer_feed_recent_thread_limit,
                post_type="daily_summary",
            )
            try:
                draft, digest, duplicate_reason = _generate_novel_draft(
                    generator,
                    payload,
                    history=daily_history,
                    settings=settings,
                )
            except ValueError:
                draft = None
                digest = "none"
                duplicate_reason = None
                content_rejections.append(
                    {
                        "post_type": "daily_summary",
                        "project": "workstation",
                        "reason": "deterministic_content_gate_rejected",
                    }
                )
        else:
            draft = None
            digest = "none"
            duplicate_reason = None
        if draft is None and duplicate_reason:
            novelty_rejections.append(
                {
                    "post_type": "daily_summary",
                    "project": "workstation",
                    "reason": duplicate_reason,
                }
            )
        elif draft is not None:
            manifest = {
                "local_date": local_date,
                "activity_thread_ids": [str(post.id) for post in activity_roots],
                "source_catalog": payload["sources"],
                "embedding_revision": settings.embedding_revision,
                "generator": {
                    "provider": generator.provider,
                    "model": generator.model,
                    "model_digest": digest,
                    "prompt_version": settings.developer_feed_prompt_version,
                },
                "screenshot_recommendation": None,
            }
            embedded = [
                post.source_embedding_to
                for post in activity_roots
                if post.source_embedding_to is not None
            ]
            created.extend(
                _add_thread(
                    session,
                    key=f"daily:{local_date}",
                    project="workstation",
                    post_type="daily_summary",
                    draft=draft,
                    manifest=manifest,
                    embedded_from=min(embedded) if embedded else None,
                    embedded_to=max(embedded) if embedded else None,
                    settings=settings,
                    created_at=now,
                )
            )
            daily_threads = 1

    session.flush()
    return {
        "state": (
            "published"
            if created
            else "rejected_duplicate"
            if novelty_rejections
            else "rejected_content"
            if content_rejections
            else "idle_no_shareable_daily_sources"
        ),
        "activity_threads": activity_threads,
        "daily_threads": daily_threads,
        "posts": len(created),
        "novelty_rejections": novelty_rejections,
        "content_rejections": content_rejections,
    }


def run(settings: Settings) -> int:
    assert_mount_guards(settings)
    provider: OllamaGenerationProvider | None = None
    try:
        with SessionLocal() as session:
            due = inspect_due(session, settings)
            if not due["pending_sources"] and not due["daily_due"]:
                print(json.dumps({"state": "idle", **due}, ensure_ascii=False))
                return 0
            provider = _provider(settings, keep_alive="2m")
            totals = {
                "activity_threads": 0,
                "daily_threads": 0,
                "posts": 0,
                "batches": 0,
                "novelty_rejections": 0,
                "content_rejections": 0,
            }
            excluded_projects: set[str] = set()
            skip_daily = False
            for _ in range(settings.developer_feed_max_batches_per_run):
                result = publish_once(
                    session,
                    settings,
                    provider=provider,
                    excluded_projects=excluded_projects,
                    skip_daily=skip_daily,
                )
                session.commit()
                totals["novelty_rejections"] += len(
                    result.get("novelty_rejections", [])
                )
                content_rejections = result.get("content_rejections", [])
                totals["content_rejections"] += len(content_rejections)
                for rejection in content_rejections:
                    project = str(rejection.get("project") or "")
                    if project == "workstation":
                        skip_daily = True
                    elif project:
                        excluded_projects.add(project)
                if result["state"] == "rejected_content" and content_rejections:
                    continue
                if result["state"] != "published":
                    break
                totals["activity_threads"] += int(result["activity_threads"])
                totals["daily_threads"] += int(result["daily_threads"])
                totals["posts"] += int(result["posts"])
                totals["batches"] += 1
                due = inspect_due(session, settings)
                if not due["pending_sources"] and not due["daily_due"]:
                    break
            remaining = inspect_due(session, settings)
            metrics = provider.performance_metrics()
        print(
            json.dumps(
                {
                    "state": (
                        "published"
                        if totals["posts"]
                        else "rejected_duplicate"
                        if totals["novelty_rejections"]
                        else "rejected_content"
                        if totals["content_rejections"]
                        else "idle"
                    ),
                    **totals,
                    "remaining_pending_sources": remaining["pending_sources"],
                    "remaining_daily_due": remaining["daily_due"],
                    "model_performance": metrics,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        if provider is not None:
            provider.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    settings = get_settings()
    with service_pid():
        return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
