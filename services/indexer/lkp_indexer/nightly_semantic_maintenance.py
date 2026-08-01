"""Run semantic deduplication and retrieval verification in one GPU reservation."""

from __future__ import annotations

import json

from lkp.db import SessionLocal
from lkp.settings import get_settings

from .cli import get_embedder
from .embedding_recovery_probe import run as run_recovery_probe
from .embedding_reindex import (
    run_with_embedder as run_document_reindex,
)
from .embedding_reindex import (
    wait_for_ingest_quiescence,
)
from .knowledge_dedup import run_once as run_dedup
from .repository_embedding_reindex import (
    run_with_embedder as run_repository_reindex,
)
from .service_runtime import assert_mount_guards, service_pid


def run() -> tuple[dict, int]:
    settings = get_settings()
    if not settings.embedding_timeout_circuit_bypass:
        raise RuntimeError("nightly semantic maintenance requires GPU circuit bypass")
    assert_mount_guards(settings)
    embedder = get_embedder(settings, deterministic=False)
    result: dict = {}
    failures = 0
    try:
        ingest = wait_for_ingest_quiescence()
        result["ingest"] = ingest
        if ingest["state"] != "quiescent":
            failures += 1
            result["document_reindex"] = {"state": "skipped_ingest_busy"}
        else:
            try:
                result["document_reindex"] = run_document_reindex(
                    settings,
                    embedder,
                    None,
                )
            except Exception as exc:
                failures += 1
                result["document_reindex"] = {
                    "state": "failed",
                    "error_type": type(exc).__name__,
                }

        try:
            result["repository_reindex"] = run_repository_reindex(
                settings,
                embedder,
                None,
            )
        except Exception as exc:
            failures += 1
            result["repository_reindex"] = {
                "state": "failed",
                "error_type": type(exc).__name__,
            }

        try:
            with SessionLocal() as session:
                result["deduplication"] = run_dedup(session, settings, embedder)
                session.commit()
        except Exception as exc:
            failures += 1
            result["deduplication"] = {
                "state": "failed",
                "error_type": type(exc).__name__,
            }

        probe = run_recovery_probe(embedder=embedder)
        result["semantic_validation"] = probe
        if probe.get("state") != "verified":
            failures += 1
    finally:
        embedder.close()
    metrics = getattr(embedder, "performance_metrics", None)
    result["model_performance"] = metrics() if metrics is not None else None
    result["state"] = "succeeded" if failures == 0 else "completed_with_errors"
    result["failed_stages"] = failures
    return result, failures


def main() -> int:
    with service_pid():
        result, failures = run()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
