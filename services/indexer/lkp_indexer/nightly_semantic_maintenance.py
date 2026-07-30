"""Run semantic deduplication and retrieval verification in one GPU reservation."""

from __future__ import annotations

import json

from lkp.db import SessionLocal
from lkp.settings import get_settings

from .cli import get_embedder
from .embedding_recovery_probe import run as run_recovery_probe
from .knowledge_dedup import run_once as run_dedup
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
