"""One-shot reindexer for documents deferred by the CPU embedding circuit.

This module has no GPU access itself. The Compose profile that invokes it is
started only by the Windows `gpuq` submission wrapper and talks to a temporary
GPU-backed Ollama service over the private Compose network.
"""

import argparse
import json
from datetime import datetime, timezone

from lkp.db import SessionLocal
from lkp.models import Document, DocumentState, DocumentVersion
from lkp.settings import get_settings
from sqlalchemy import select, text

from .cli import get_embedder
from .worker import _embed_missing


def run(limit: int | None = None) -> dict[str, int | str]:
    settings = get_settings()
    if not settings.embedding_timeout_circuit_bypass:
        raise RuntimeError("GPU reindex requires LKP_EMBEDDING_TIMEOUT_CIRCUIT_BYPASS=true")
    embedder = get_embedder(settings, deterministic=False)
    try:
        return _run(settings, embedder, limit)
    finally:
        embedder.close()


def _run(settings, embedder, limit: int | None) -> dict[str, int | str]:
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
    return {**result, "completed_at": datetime.now(timezone.utc).isoformat()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    print(json.dumps(run(args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
