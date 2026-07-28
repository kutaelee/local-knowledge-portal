from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lkp.models import ActivityEvent, EvidenceRecord, IngestEvent, IngestJob, JobStatus
from sqlalchemy import exists, select
from sqlalchemy.orm import Session


def roll_up_activity_details(
    session: Session,
    *,
    retention_days: int,
    now: datetime | None = None,
) -> int:
    """Hide old, successful tool detail while retaining summaries and evidence.

    This is deliberately non-destructive. User prompts, turn outcomes, failures,
    and every activity linked to evidence remain visible. The detailed row stays
    addressable for audit and can be restored by removing the metadata marker.
    """

    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=retention_days)
    linked_evidence = exists(
        select(EvidenceRecord.id).where(EvidenceRecord.activity_id == ActivityEvent.id)
    )
    rows = list(
        session.scalars(
            select(ActivityEvent).where(
                ActivityEvent.event_type == "PostToolUse",
                ActivityEvent.occurred_at < cutoff,
                ActivityEvent.exit_code == 0,
                ~linked_evidence,
            )
        )
    )
    changed = 0
    for row in rows:
        metadata = dict(row.metadata_json or {})
        if metadata.get("retention_state") == "rolled_up":
            continue
        metadata.update(
            {
                "retention_state": "rolled_up",
                "rolled_up_at": current.isoformat(),
                "retention_days": retention_days,
                "retention_reason": "successful_unlinked_tool_detail",
            }
        )
        row.metadata_json = metadata
        changed += 1
    session.flush()
    return changed


def roll_up_operational_details(
    session: Session,
    *,
    terminal_job_retention_days: int,
    ingest_event_retention_days: int,
    now: datetime | None = None,
) -> dict[str, int]:
    """Place high-volume, low-risk operational detail into a logical cold tier.

    This deliberately does **not** delete queue, event, source, evidence, or
    knowledge rows.  A successful/cancelled job and routine indexed event keep
    their identifiers, timestamps, and provenance, but default dashboard
    queries stop scanning them after the configured window.  Failed and
    dead-letter work remains hot indefinitely until an operator explicitly
    chooses an archival/export policy.
    """

    current = now or datetime.now(timezone.utc)
    job_cutoff = current - timedelta(days=terminal_job_retention_days)
    event_cutoff = current - timedelta(days=ingest_event_retention_days)
    job_changed = 0
    event_changed = 0

    jobs = list(
        session.scalars(
            select(IngestJob).where(
                IngestJob.status.in_([JobStatus.succeeded, JobStatus.cancelled]),
                IngestJob.finished_at.is_not(None),
                IngestJob.finished_at < job_cutoff,
            )
        )
    )
    for row in jobs:
        details = dict(row.error_details or {})
        if details.get("retention_state") == "rolled_up":
            continue
        details.update(
            {
                "retention_state": "rolled_up",
                "rolled_up_at": current.isoformat(),
                "retention_days": terminal_job_retention_days,
                "retention_reason": "terminal_success_or_cancelled_job",
            }
        )
        row.error_details = details
        job_changed += 1

    # Failure, delete, rename, and restore events are operational evidence and
    # stay visible.  Only routine terminal indexing noise enters the cold tier.
    events = list(
        session.scalars(
            select(IngestEvent).where(
                IngestEvent.event_type.in_(["indexed", "ignored", "unsupported"]),
                IngestEvent.created_at < event_cutoff,
            )
        )
    )
    for row in events:
        details = dict(row.details or {})
        if details.get("retention_state") == "rolled_up":
            continue
        details.update(
            {
                "retention_state": "rolled_up",
                "rolled_up_at": current.isoformat(),
                "retention_days": ingest_event_retention_days,
                "retention_reason": "routine_terminal_ingest_event",
            }
        )
        row.details = details
        event_changed += 1

    session.flush()
    return {"jobs": job_changed, "events": event_changed}
