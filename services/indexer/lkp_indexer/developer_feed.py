from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
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
from lkp.settings import Settings, get_settings
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .service_runtime import assert_mount_guards, service_pid

_LOCK_KEY = "developer_feed.publisher"


def _normalize(value: str) -> str:
    clean = " ".join(value.replace("\x00", " ").split())
    return clean


def _split_text(value: str, limit: int) -> list[str]:
    remaining = _normalize(value)
    parts: list[str] = []
    while len(remaining) > limit:
        boundary = remaining.rfind(" ", 0, limit + 1)
        if boundary < max(1, limit // 2):
            boundary = limit
        parts.append(remaining[:boundary].rstrip())
        remaining = remaining[boundary:].lstrip()
    if remaining:
        parts.append(remaining)
    return parts or ["—"]


def _expand_messages(
    messages: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    expanded: list[tuple[str, str]] = []
    for content_ko, content_en in messages:
        ko_parts = _split_text(content_ko, 140)
        en_parts = _split_text(content_en, 280)
        part_count = max(len(ko_parts), len(en_parts))
        for index in range(part_count):
            expanded.append(
                (
                    ko_parts[index] if index < len(ko_parts) else "↳ 영문 내용 계속",
                    en_parts[index] if index < len(en_parts) else "↳ Continued in Korean",
                )
            )
    return expanded


def _basename(value: str) -> str:
    return PurePath(value.replace("\\", "/")).name


def _work_copy(
    project: str,
    file_count: int,
    check_count: int,
    significance: list[str],
) -> tuple[str, str]:
    kind_ko = (
        "오류를 복구한"
        if "verified_failure_and_recovery" in significance
        else "운영·설정을 다듬은"
        if "operational_or_configuration_change" in significance
        else "구현을 진전시킨"
    )
    kind_en = (
        "recovered a verified failure"
        if "verified_failure_and_recovery" in significance
        else "refined operations/configuration"
        if "operational_or_configuration_change" in significance
        else "advanced the implementation"
    )
    ko = (
        f"작업 기록 · {project}에서 {kind_ko} 작업. 임베딩 완료 파일 {file_count}개와 "
        f"실행 검증 {check_count}건을 근거로 기록했다. 이 PC의 프로젝트를 오늘도 한 칸 전진."
    )
    en = (
        f"Work log · {kind_en.capitalize()} in {project}. Logged {file_count} newly embedded "
        f"file(s) with {check_count} execution check(s). Another small step forward "
        "on this workstation."
    )
    return _normalize(ko), _normalize(en)


def _evidence_copy(files: list[str], check_count: int) -> tuple[str, str]:
    shown = ", ".join(_basename(path) for path in files[:4]) or "—"
    extra = max(0, len(files) - 4)
    suffix_ko = f" 외 {extra}개" if extra else ""
    suffix_en = f" +{extra} more" if extra else ""
    return (
        _normalize(f"근거 · {shown}{suffix_ko} · 통과한 실행 검증 {check_count}건"),
        _normalize(f"Evidence · {shown}{suffix_en} · {check_count} passing execution check(s)"),
    )


def _daily_copy(
    local_date: str,
    activity_count: int,
    projects: list[str],
    file_count: int,
) -> tuple[str, str]:
    project_text = ", ".join(projects[:4]) or "—"
    if activity_count:
        ko = (
            f"오늘의 정리 · {local_date} 작업 마감. {project_text}에서 임베딩 근거가 확인된 "
            f"작업 {activity_count}건, 임베딩 파일 {file_count}개를 기록했다. "
            "내일 이어갈 수 있도록 오늘의 흔적을 정리 완료."
        )
        en = (
            f"Daily wrap · {local_date}: {activity_count} evidence-backed update(s) "
            f"across {project_text}, covering {file_count} embedded file(s). "
            "The workstation log is ready for tomorrow."
        )
    else:
        ko = (
            f"오늘의 정리 · {local_date} 작업 마감. 18시 기준 새로 임베딩되고 검증된 작업은 "
            "없었다. 근거 없는 진행 상황은 게시하지 않고 오늘 기록을 닫는다."
        )
        en = (
            f"Daily wrap · {local_date}: no newly embedded, verified work by 18:00. "
            "Closing the log without inventing progress."
        )
    return _normalize(ko), _normalize(en)


def _embedded_documents(
    session: Session,
    journal: ProjectJournalEntry,
    settings: Settings,
) -> list[dict[str, object]]:
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
        .group_by(Document.id, DocumentVersion.id, Document.filename)
    ).all()
    return [
        {
            "document_id": str(document_id),
            "document_version_id": str(version_id),
            "filename": filename,
            "embedded_at": embedded_at,
        }
        for document_id, version_id, filename, embedded_at in rows
    ]


def _add_thread(
    session: Session,
    *,
    key: str,
    project: str,
    post_type: str,
    messages: list[tuple[str, str]],
    manifest: dict[str, object],
    embedded_from: datetime | None,
    embedded_to: datetime | None,
    persona_version: str,
    created_at: datetime,
) -> list[DeveloperFeedPost]:
    posts: list[DeveloperFeedPost] = []
    root_id = uuid.uuid4()
    previous_id: uuid.UUID | None = None
    for sequence, (content_ko, content_en) in enumerate(
        _expand_messages(messages)
    ):
        post_id = root_id if sequence == 0 else uuid.uuid4()
        post = DeveloperFeedPost(
            id=post_id,
            publication_key=f"{key}:{sequence}",
            project_key=project,
            post_type=post_type,
            thread_root_id=root_id,
            reply_to_id=previous_id,
            sequence=sequence,
            content_ko=content_ko,
            content_en=content_en,
            source_manifest_json=manifest,
            source_embedding_from=embedded_from,
            source_embedding_to=embedded_to,
            persona_version=persona_version,
            created_at=created_at,
        )
        session.add(post)
        posts.append(post)
        previous_id = post_id
    return posts


def publish_once(
    session: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> dict[str, object]:
    now = now or datetime.now(timezone.utc)
    if not settings.developer_feed_enabled:
        return {"state": "disabled", "activity_threads": 0, "daily_threads": 0}
    acquired = session.execute(
        text("select pg_try_advisory_xact_lock(hashtext(:key))"),
        {"key": _LOCK_KEY},
    ).scalar_one()
    if not acquired:
        return {"state": "standby_lock_held", "activity_threads": 0, "daily_threads": 0}

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
    created: list[DeveloperFeedPost] = []
    activity_threads = 0
    eligible_journals: list[
        tuple[
            ProjectJournalEntry,
            list[dict[str, object]],
            list[datetime],
        ]
    ] = []
    for journal in journals:
        root_key = f"activity:{journal.id}:0"
        if session.scalar(
            select(DeveloperFeedPost.id).where(
                DeveloperFeedPost.publication_key == root_key
            )
        ):
            continue
        documents = _embedded_documents(session, journal, settings)
        embedded_times = [
            item["embedded_at"]
            for item in documents
            if isinstance(item.get("embedded_at"), datetime)
        ]
        if not embedded_times or max(embedded_times) < cursor:
            continue
        eligible_journals.append((journal, documents, embedded_times))

    eligible_journals.sort(
        key=lambda item: (
            max(item[2]),
            item[0].occurred_at,
            str(item[0].id),
        )
    )
    for journal, documents, embedded_times in eligible_journals[
        : settings.developer_feed_max_sources_per_run
    ]:
        checks = sum(
            1
            for item in (journal.verification_json or [])
            if item.get("exit_code") == 0
        )
        manifest = {
            "journal_id": str(journal.id),
            "verification_status": journal.verification_status,
            "embedding_revision": settings.embedding_revision,
            "documents": [
                {
                    **item,
                    "embedded_at": item["embedded_at"].isoformat(),
                }
                for item in documents
            ],
            "changed_file_count": len(journal.changed_files),
            "passing_check_count": checks,
        }
        created.extend(
            _add_thread(
                session,
                key=f"activity:{journal.id}",
                project=journal.project_key,
                post_type="activity",
                messages=[
                    _work_copy(
                        journal.project_key,
                        len(documents),
                        checks,
                        list(journal.significance_reasons or []),
                    ),
                    _evidence_copy(list(journal.changed_files), checks),
                ],
                manifest=manifest,
                embedded_from=min(embedded_times),
                embedded_to=max(embedded_times),
                persona_version=settings.developer_feed_persona_version,
                created_at=now,
            )
        )
        activity_threads += 1
    backlog_remaining = max(
        0,
        len(eligible_journals) - settings.developer_feed_max_sources_per_run,
    )

    local_tz = ZoneInfo(settings.developer_feed_timezone)
    local_now = now.astimezone(local_tz)
    local_date = local_now.date().isoformat()
    daily_threads = 0
    daily_key = f"daily:{local_date}:0"
    if (
        local_now.hour >= settings.developer_feed_daily_hour
        and backlog_remaining == 0
        and not session.scalar(
            select(DeveloperFeedPost.id).where(
                DeveloperFeedPost.publication_key == daily_key
            )
        )
    ):
        day_start = datetime.combine(
            local_now.date(), datetime.min.time(), tzinfo=local_tz
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
        projects = sorted({post.project_key for post in activity_roots})
        file_count = sum(
            len((post.source_manifest_json or {}).get("documents", []))
            for post in activity_roots
        )
        document_ids = sorted(
            {
                str(document.get("document_id"))
                for post in activity_roots
                for document in (post.source_manifest_json or {}).get("documents", [])
                if document.get("document_id")
            }
        )
        embedded = [
            post.source_embedding_to
            for post in activity_roots
            if post.source_embedding_to is not None
        ]
        daily_manifest = {
            "local_date": local_date,
            "activity_thread_ids": [str(post.id) for post in activity_roots],
            "document_ids": document_ids,
            "embedding_revision": settings.embedding_revision,
            "activity_count": len(activity_roots),
            "changed_file_count": file_count,
        }
        created.extend(
            _add_thread(
                session,
                key=f"daily:{local_date}",
                project="workstation",
                post_type="daily_summary",
                messages=[
                    _daily_copy(
                        local_date,
                        len(activity_roots),
                        projects,
                        file_count,
                    )
                ],
                manifest=daily_manifest,
                embedded_from=min(embedded) if embedded else None,
                embedded_to=max(embedded) if embedded else None,
                persona_version=settings.developer_feed_persona_version,
                created_at=now,
            )
        )
        daily_threads = 1
    session.flush()
    return {
        "state": "published" if created else "idle",
        "activity_threads": activity_threads,
        "daily_threads": daily_threads,
        "posts": len(created),
        "backlog_remaining": backlog_remaining,
        "embedding_cursor": cursor.isoformat(),
    }


def run(settings: Settings, *, once: bool) -> int:
    assert_mount_guards(settings)
    while True:
        with SessionLocal() as session:
            result = publish_once(session, settings)
            session.commit()
        if once:
            print(json.dumps(result, ensure_ascii=False))
            return 0
        time.sleep(settings.developer_feed_poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    with service_pid():
        return run(settings, once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
