from datetime import datetime, timedelta, timezone

from lkp.models import IngestJob
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session


def timeout_circuit_state(
    session: Session,
    *,
    threshold: int,
    window_seconds: int,
    runtime_mode: str = "enabled",
) -> dict[str, int | bool | str | None]:
    """Report timeout and operator-held embedding deferral as one safe state."""

    since = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    count, last_seen = session.execute(
        select(func.count(), func.max(IngestJob.updated_at)).where(
            or_(
                IngestJob.error_type == "ReadTimeout",
                IngestJob.error_details["recovered_error"]["type"].astext
                == "ReadTimeout",
            ),
            IngestJob.updated_at >= since,
        )
    ).one()
    recent_timeouts = int(count or 0)
    deferred_for_gpu_recovery = runtime_mode == "deferred_gpu_recovery"
    return {
        "open": deferred_for_gpu_recovery or recent_timeouts >= threshold,
        "mode": runtime_mode,
        "reason": (
            "gpu_recovery_pending"
            if deferred_for_gpu_recovery
            else "timeout_threshold_exceeded"
            if recent_timeouts >= threshold
            else None
        ),
        "recent_timeouts": recent_timeouts,
        "threshold": threshold,
        "window_seconds": window_seconds,
        "last_timeout_at": last_seen.isoformat() if last_seen else None,
    }


def timeout_circuit_reason(session: Session, settings) -> str | None:
    if settings.embedding_timeout_circuit_bypass:
        return None
    state = timeout_circuit_state(
        session,
        threshold=settings.embedding_timeout_circuit_threshold,
        window_seconds=settings.embedding_timeout_circuit_window_seconds,
        runtime_mode=settings.embedding_runtime_mode,
    )
    if state["reason"] == "gpu_recovery_pending":
        return "embedding_gpu_recovery_pending"
    return "ollama_timeout_circuit_open" if state["open"] else None
