from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from lkp.models import Document, DocumentState, IngestEvent, SourceRoot
from lkp.settings import Settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ignore import IgnoreRules, IncludeRules
from .paths import idempotency_key
from .queue import enqueue
from .repository_freshness import reconcile_repository_snapshots
from .scanner import scan_root


@dataclass(slots=True)
class ReconcileStats:
    visited: int = 0
    queued: int = 0
    missing: int = 0
    ignored: int = 0
    errors: int = 0
    repositories_checked: int = 0
    repositories_stale: int = 0
    repository_jobs_queued: int = 0


def reconcile_root(session: Session, source_root: SourceRoot, settings: Settings) -> ReconcileStats:
    scan = scan_root(session, source_root, settings.max_file_bytes)
    stats = ReconcileStats(
        visited=scan.visited,
        queued=scan.queued,
        errors=scan.errors,
    )
    root_path = Path(source_root.canonical_path)
    ignore_rules = IgnoreRules(root_path, source_root.exclude_patterns)
    include_rules = IncludeRules(source_root.include_patterns)
    documents = session.scalars(
        select(Document).where(
            Document.source_root_id == source_root.id,
            Document.state == DocumentState.active,
        )
    ).all()
    for document in documents:
        path = Path(document.canonical_path)
        try:
            relative = path.relative_to(root_path).as_posix()
        except ValueError:
            continue
        if ignore_rules.matches(relative) or not include_rules.matches(relative):
            document.state = DocumentState.ignored
            document.last_seen_at = datetime.now(timezone.utc)
            stats.ignored += 1
            session.add(
                IngestEvent(
                    source_root_id=source_root.id,
                    document_id=document.id,
                    event_type="ignored",
                    path=document.canonical_path,
                    details={
                        "reason": "policy_reconciliation",
                        "relative_path": relative,
                    },
                )
            )
            continue
        if path.exists():
            continue
        key = idempotency_key(
            str(source_root.id),
            document.canonical_path,
            0,
            0,
        )
        if enqueue(
            session,
            key=key,
            source_root_id=source_root.id,
            canonical_path=document.canonical_path,
            job_type="reconcile_delete",
            details={"reason": "missing_during_reconciliation"},
        ):
            stats.queued += 1
        stats.missing += 1
    repository_stats = reconcile_repository_snapshots(session, source_root)
    stats.repositories_checked = repository_stats.checked
    stats.repositories_stale = repository_stats.stale
    stats.repository_jobs_queued = repository_stats.queued
    stats.errors += repository_stats.errors
    source_root.last_reconciled_at = datetime.now(timezone.utc)
    session.add(
        IngestEvent(
            source_root_id=source_root.id,
            event_type="reconciled",
            path=source_root.canonical_path,
            details={
                "visited": stats.visited,
                "queued": stats.queued,
                "missing": stats.missing,
                "ignored": stats.ignored,
                "errors": stats.errors,
                "repositories_checked": stats.repositories_checked,
                "repositories_stale": stats.repositories_stale,
                "repository_jobs_queued": stats.repository_jobs_queued,
            },
        )
    )
    return stats
