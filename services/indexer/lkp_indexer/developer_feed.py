from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from typing import Any
from zoneinfo import ZoneInfo

import httpx
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
from lkp.settings import Settings, get_settings
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .generation import DeveloperFeedDraft, GenerationProvider, OllamaGenerationProvider
from .service_runtime import assert_mount_guards, service_pid

_LOCK_KEY = "developer_feed.publisher.v2"


@dataclass(frozen=True, slots=True)
class FeedBatch:
    journals: list[ProjectJournalEntry]
    documents: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    embedded_from: datetime
    embedded_to: datetime


def _basename(value: str) -> str:
    return PurePath(value.replace("\\", "/")).name


def _embedded_documents(
    session: Session,
    journal: ProjectJournalEntry,
    settings: Settings,
) -> list[dict[str, Any]]:
    filenames = sorted(
        {_basename(path).casefold() for path in journal.changed_files if _basename(path)}
    )
    if not filenames:
        return []
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
) -> FeedBatch | None:
    last_embedding = session.scalar(
        select(func.max(DeveloperFeedPost.source_embedding_to)).where(
            DeveloperFeedPost.post_type == "activity"
        )
    )
    cursor = last_embedding or (
        now - timedelta(hours=settings.developer_feed_initial_lookback_hours)
    )
    journals = list(
        session.scalars(
            select(ProjectJournalEntry)
            .where(
                ProjectJournalEntry.verification_status == "VERIFIED",
                ProjectJournalEntry.occurred_at
                >= now - timedelta(hours=settings.developer_feed_initial_lookback_hours),
            )
            .order_by(ProjectJournalEntry.occurred_at, ProjectJournalEntry.id)
            .limit(1000)
        )
    )
    eligible: list[tuple[ProjectJournalEntry, list[dict[str, Any]]]] = []
    for journal in journals:
        documents = _embedded_documents(session, journal, settings)
        if documents and max(item["embedded_at"] for item in documents) > cursor:
            eligible.append((journal, documents))
    eligible.sort(
        key=lambda item: (
            max(document["embedded_at"] for document in item[1]),
            item[0].occurred_at,
            str(item[0].id),
        )
    )
    eligible = eligible[: settings.developer_feed_max_sources_per_run]
    if not eligible:
        return None

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
    daily_key = f"daily:{local_now.date().isoformat()}:0"
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
                "post_source_ids": message.source_ids,
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


def _provider(settings: Settings) -> OllamaGenerationProvider:
    return OllamaGenerationProvider(
        settings.generation_base_url,
        settings.developer_feed_model,
        settings.developer_feed_model_digest,
        settings.developer_feed_timeout_seconds,
        temperature=0.2,
        context_window=settings.developer_feed_context_window,
        keep_alive="0",
    )


def _bounded_payload(payload: dict[str, Any], max_chars: int) -> dict[str, Any]:
    while len(json.dumps(payload, ensure_ascii=False)) > max_chars:
        documents = [
            source
            for source in payload["sources"]
            if source.get("source_type") == "current_embedded_document"
        ]
        if documents:
            payload["sources"].remove(documents[-1])
            continue
        break
    return payload


def publish_once(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    provider: GenerationProvider | None = None,
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

    batch = _eligible_batch(session, settings, now=now)
    local_now = now.astimezone(ZoneInfo(settings.developer_feed_timezone))
    local_date = local_now.date().isoformat()
    daily_key = f"daily:{local_date}:0"
    daily_due = local_now.hour >= settings.developer_feed_daily_hour and not session.scalar(
        select(DeveloperFeedPost.id).where(
            DeveloperFeedPost.publication_key == daily_key
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

    if batch is not None:
        payload = _bounded_payload(
            {
                "post_type": "information_update",
                "window": {
                    "embedded_from": batch.embedded_from.isoformat(),
                    "embedded_to": batch.embedded_to.isoformat(),
                },
                "sources": list(batch.sources),
            },
            settings.developer_feed_max_input_chars,
        )
        draft, digest = generator.write_developer_feed(
            payload,
            prompt_version=settings.developer_feed_prompt_version,
        )
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
        projects = sorted({journal.project_key for journal in batch.journals})
        created.extend(
            _add_thread(
                session,
                key=f"activity:{batch_hash}",
                project=projects[0] if len(projects) == 1 else "workstation",
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
        day_start = datetime.combine(
            local_now.date(), datetime.min.time(), tzinfo=local_now.tzinfo
        ).astimezone(timezone.utc)
        activity_roots = list(
            session.scalars(
                select(DeveloperFeedPost).where(
                    DeveloperFeedPost.post_type == "activity",
                    DeveloperFeedPost.sequence == 0,
                    DeveloperFeedPost.created_at >= day_start,
                    DeveloperFeedPost.created_at <= now,
                )
            )
        )
        source_catalog: list[dict[str, Any]] = []
        for post in activity_roots:
            source_catalog.extend(
                (post.source_manifest_json or {}).get("source_catalog", [])
            )
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
                    "sources": sources,
                },
                settings.developer_feed_max_input_chars,
            )
            draft, digest = generator.write_developer_feed(
                payload,
                prompt_version=settings.developer_feed_prompt_version,
            )
        else:
            digest = settings.developer_feed_model_digest
            draft = DeveloperFeedDraft(
                posts=[
                    {
                        "content_ko": (
                            f"{local_date} 오늘은 새로 확인된 프로젝트 정보가 없어 "
                            "정리글만 남긴다. 근거 없는 진행 상황은 덧붙이지 않았다."
                        ),
                        "content_en": (
                            f"{local_date}: no new project information was verified today. "
                            "Closing the daily note without inventing progress."
                        ),
                        "source_ids": ["NO_UPDATE"],
                    }
                ]
            )
            payload = {
                "post_type": "daily_summary",
                "local_date": local_date,
                "sources": [{"id": "NO_UPDATE", "source_type": "no_verified_update"}],
            }
        manifest = {
            "local_date": local_date,
            "activity_thread_ids": [str(post.id) for post in activity_roots],
            "source_catalog": payload["sources"],
            "embedding_revision": settings.embedding_revision,
            "generator": {
                "provider": generator.provider if sources else "deterministic",
                "model": generator.model if sources else "none",
                "model_digest": digest if sources else "none",
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
        "state": "published",
        "activity_threads": activity_threads,
        "daily_threads": daily_threads,
        "posts": len(created),
    }


def _unload_model(settings: Settings) -> None:
    try:
        httpx.post(
            f"{settings.generation_base_url.rstrip('/')}/api/generate",
            json={"model": settings.developer_feed_model, "keep_alive": 0},
            timeout=15,
        ).raise_for_status()
    except Exception as exc:
        print(f"warning: failed to unload {settings.developer_feed_model}: {exc}")


def run(settings: Settings) -> int:
    assert_mount_guards(settings)
    try:
        with SessionLocal() as session:
            due = inspect_due(session, settings)
            if not due["pending_sources"] and not due["daily_due"]:
                print(json.dumps({"state": "idle", **due}, ensure_ascii=False))
                return 0
            result = publish_once(session, settings)
            session.commit()
        print(json.dumps(result, ensure_ascii=False))
        return 0
    finally:
        _unload_model(settings)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    settings = get_settings()
    with service_pid():
        return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
