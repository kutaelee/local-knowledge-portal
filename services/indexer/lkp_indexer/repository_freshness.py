from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog
from lkp.models import JobStatus, SourceRoot
from sqlalchemy import text
from sqlalchemy.orm import Session

from .queue import enqueue
from .repository_analysis.discovery import discover

logger = structlog.get_logger()


@dataclass(slots=True)
class RepositoryFreshnessStats:
    checked: int = 0
    stale: int = 0
    queued: int = 0
    errors: int = 0


def snapshot_is_stale(current_hash: str | None, discovered_hash: str) -> bool:
    return not current_hash or current_hash != discovered_hash


def reconcile_repository_snapshots(
    session: Session,
    source_root: SourceRoot,
) -> RepositoryFreshnessStats:
    """Compare registered repositories with disk and queue changed snapshots.

    Document include/exclude rules intentionally do not apply here: repository
    analysis owns code indexing, while the document scanner owns prose.
    """

    stats = RepositoryFreshnessStats()
    root_path = Path(source_root.canonical_path).resolve(strict=True)
    projects = session.execute(
        text(
            """
            SELECT p.id, p.local_source_reference, p.category,
                   s.id AS snapshot_id, s.source_hash
            FROM repository_project p
            LEFT JOIN LATERAL (
              SELECT rs.id, rs.source_hash
              FROM repository_snapshot rs
              WHERE rs.project_id = p.id
              ORDER BY rs.created_at DESC
              LIMIT 1
            ) s ON true
            WHERE p.status = 'ACTIVE'
              AND (
                p.local_source_reference = :root
                OR p.local_source_reference LIKE :prefix
              )
            """
        ),
        {
            "root": str(root_path),
            "prefix": f"{root_path.as_posix().rstrip('/')}/%",
        },
    ).mappings()
    for project in projects:
        source_path = Path(str(project["local_source_reference"]))
        try:
            resolved = source_path.resolve(strict=True)
            if not resolved.is_dir() or not resolved.is_relative_to(root_path):
                continue
            discovered = discover(resolved, allowed_roots=[root_path])
            stats.checked += 1
            if not snapshot_is_stale(project["source_hash"], discovered.source_hash):
                continue
            stats.stale += 1
            if project["snapshot_id"] is not None:
                session.execute(
                    text(
                        """
                        UPDATE repository_snapshot
                        SET stale = true
                        WHERE id = :snapshot_id AND stale = false
                        """
                    ),
                    {"snapshot_id": project["snapshot_id"]},
                )
            latest_job = session.execute(
                text(
                    """
                    SELECT status, finished_at, created_at
                    FROM ingest_job
                    WHERE canonical_path = :path
                      AND job_type = 'repository_analysis'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"path": str(resolved)},
            ).mappings().one_or_none()
            if latest_job is not None:
                status = latest_job["status"]
                status_value = status.value if hasattr(status, "value") else str(status)
                active = status_value in {
                    JobStatus.pending.value,
                    JobStatus.leased.value,
                    JobStatus.processing.value,
                    JobStatus.failed.value,
                }
                retry_at = (
                    latest_job["finished_at"] or latest_job["created_at"]
                ) + timedelta(hours=1)
                if active or retry_at > datetime.now(timezone.utc):
                    continue
            key_material = (
                f"{project['id']}:{discovered.source_hash}:"
                f"{datetime.now(timezone.utc):%Y%m%d%H}"
            )
            job = enqueue(
                session,
                key=(
                    "repository-auto:"
                    + hashlib.sha256(key_material.encode()).hexdigest()
                )[:128],
                source_root_id=source_root.id,
                canonical_path=str(resolved),
                job_type="repository_analysis",
                max_attempts=3,
                priority=40,
                coalesce_pending=True,
                details={
                    "category": str(project["category"] or "Library")[:100],
                    "requested_via": "repository_freshness_reconcile",
                    "detected_source_hash": discovered.source_hash,
                    "previous_source_hash": project["source_hash"],
                },
            )
            stats.queued += int(job is not None)
        except (OSError, ValueError) as exc:
            stats.errors += 1
            logger.warning(
                "repository_freshness_check_failed",
                source_path=str(source_path),
                error_type=type(exc).__name__,
            )
    return stats
