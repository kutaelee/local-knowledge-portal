from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from lkp.db import SessionLocal
from lkp.models import IngestEvent, IngestJob, JobStatus, SourceRoot
from lkp.settings import get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from .file_safety import source_file_rejection_reason
from .ignore import IgnoreRules


def resolve_non_retryable_dead_letters(
    session: Session,
    *,
    max_file_bytes: int,
    apply: bool = False,
) -> list[dict[str, str | bool]]:
    """Cancel only dead letters whose current input is deterministically unsupported."""

    roots = {root.id: root for root in session.scalars(select(SourceRoot))}
    results: list[dict[str, str | bool]] = []
    jobs = session.scalars(select(IngestJob).where(IngestJob.status == JobStatus.dead_letter))
    for job in jobs:
        root = roots.get(job.source_root_id)
        if root is None:
            continue
        root_path = Path(root.canonical_path)
        path = Path(job.canonical_path)
        try:
            relative = path.relative_to(root_path).as_posix()
        except ValueError:
            continue
        reason: str | None = None
        if IgnoreRules(root_path, root.exclude_patterns).matches(relative):
            reason = "ignore_rule"
        elif path.is_file():
            try:
                reason = source_file_rejection_reason(path, max_file_bytes)
            except OSError:
                continue
        if reason is None:
            continue

        results.append(
            {
                "job_id": str(job.id),
                "path": job.canonical_path,
                "reason": reason,
                "applied": apply,
            }
        )
        if not apply:
            continue

        now = datetime.now(timezone.utc)
        job.status = JobStatus.cancelled
        job.finished_at = job.finished_at or now
        job.updated_at = now
        job.leased_by = None
        job.lease_expires_at = None
        job.error_details = {
            **(job.error_details or {}),
            "resolution": "non_retryable_unsupported_input",
            "resolved_at": now.isoformat(),
            "resolved_from": JobStatus.dead_letter.value,
            "rejection_reason": reason,
        }
        session.add(
            IngestEvent(
                source_root_id=job.source_root_id,
                document_id=job.document_id,
                event_type="dead_letter_resolved",
                path=job.canonical_path,
                details={
                    "job_id": str(job.id),
                    "resolution": "non_retryable_unsupported_input",
                    "reason": reason,
                    "attempt_count": job.attempt_count,
                },
            )
        )
    session.flush()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve dead letters that are now deterministically unsupported."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Mark safe matches cancelled. Without this flag the command is a dry run.",
    )
    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal() as session:
        items = resolve_non_retryable_dead_letters(
            session,
            max_file_bytes=settings.max_file_bytes,
            apply=args.apply,
        )
        if args.apply:
            session.commit()
        else:
            session.rollback()
    print(
        json.dumps(
            {"mode": "apply" if args.apply else "dry-run", "items": items},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
