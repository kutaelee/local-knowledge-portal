from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lkp.models import ActivityEvent, EvidenceRecord
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
