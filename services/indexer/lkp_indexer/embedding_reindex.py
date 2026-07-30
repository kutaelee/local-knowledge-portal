"""One-shot reindexer for documents deferred by the CPU embedding circuit.

This module has no GPU access itself. The Compose profile that invokes it is
started only by the Windows `gpuq` submission wrapper and talks to a temporary
GPU-backed Ollama service over the private Compose network.
"""

import argparse
import json
import time
from datetime import datetime, timezone

from lkp.db import SessionLocal
from lkp.models import Document, DocumentState, DocumentVersion, IngestJob, JobStatus
from lkp.settings import get_settings
from sqlalchemy import func, or_, select, text

from .cli import get_embedder
from .worker import _embed_missing


def run(limit: int | None = None) -> dict[str, int | str]:
    settings = get_settings()
    if not settings.embedding_timeout_circuit_bypass:
        raise RuntimeError("GPU reindex requires LKP_EMBEDDING_TIMEOUT_CIRCUIT_BYPASS=true")
    embedder = get_embedder(settings, deterministic=False)
    try:
        return run_with_embedder(settings, embedder, limit)
    finally:
        embedder.close()


def wait_for_ingest_quiescence(
    *,
    timeout_seconds: int = 120,
    poll_seconds: float = 2,
    stable_checks: int = 2,
) -> dict[str, int | float | str]:
    """Wait for two quiet queue observations before freezing the reindex set."""

    started = time.monotonic()
    quiet = 0
    active = 0
    while True:
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            active = int(
                session.scalar(
                    select(func.count(IngestJob.id)).where(
                        or_(
                            IngestJob.status.in_(
                                [JobStatus.leased, JobStatus.processing]
                            ),
                            (
                                (IngestJob.status == JobStatus.pending)
                                & (IngestJob.available_at <= now)
                            ),
                        )
                    )
                )
                or 0
            )
        quiet = quiet + 1 if active == 0 else 0
        elapsed = time.monotonic() - started
        if quiet >= stable_checks:
            return {
                "state": "quiescent",
                "active_jobs": 0,
                "waited_seconds": round(elapsed, 3),
            }
        if elapsed >= timeout_seconds:
            return {
                "state": "timeout",
                "active_jobs": active,
                "waited_seconds": round(elapsed, 3),
            }
        time.sleep(poll_seconds)


def run_with_embedder(settings, embedder, limit: int | None) -> dict:
    result = {"examined": 0, "embedded": 0, "still_deferred": 0, "skipped": 0}
    with SessionLocal() as session:
        statement = (
            select(Document, DocumentVersion)
            .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
            .where(
                Document.state == DocumentState.active,
                DocumentVersion.metadata_json["embedding_status"].astext
                == "deferred_runtime",
            )
            .order_by(DocumentVersion.detected_at, Document.id)
        )
        if limit is not None:
            statement = statement.limit(limit)
        rows = list(session.execute(statement).all())
        for document, version in rows:
            result["examined"] += 1
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"embedding-reindex:{document.id}:{version.id}"},
            )
            _embed_missing(session, version, settings, embedder)
            status = (version.metadata_json or {}).get("embedding_status")
            if status == "complete":
                result["embedded"] += 1
            elif status == "deferred_runtime":
                result["still_deferred"] += 1
            else:
                result["skipped"] += 1
            session.commit()
    metrics = getattr(embedder, "performance_metrics", None)
    return {
        **result,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "model_performance": metrics() if metrics is not None else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    print(json.dumps(run(args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
