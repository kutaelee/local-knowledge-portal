"""Verify semantic and hybrid retrieval inside a gpuq-admitted Ollama batch.

The normal API deliberately stays in ``deferred_gpu_recovery`` after a CPU
embedding incident.  This one-shot probe therefore provides the evidence needed
to validate repaired vectors without silently waking the CPU embedding service.
It writes a small atomic snapshot containing no source text or command line.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lkp.db import SessionLocal
from lkp.models import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentState,
    DocumentVersion,
    SourceRoot,
)
from lkp.schemas import SearchRequest
from lkp.search import search
from lkp.settings import get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from .cli import get_embedder
from .embedding import Embedder


def write_snapshot(path: Path, payload: dict[str, Any]) -> None:
    """Atomically publish a bounded, source-text-free recovery result."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _anchor(session: Session, revision: str) -> str | None:
    """Return a current production chunk that is already vector-indexed."""

    return session.scalar(
        select(DocumentChunk.content)
        .join(ChunkEmbedding, ChunkEmbedding.chunk_id == DocumentChunk.id)
        .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
        .join(Document, Document.current_version_id == DocumentVersion.id)
        .join(SourceRoot, SourceRoot.id == Document.source_root_id)
        .where(
            Document.state == DocumentState.active,
            SourceRoot.data_scope == "production",
            ChunkEmbedding.embedding_revision == revision,
        )
        .order_by(ChunkEmbedding.created_at.desc(), DocumentChunk.id)
        .limit(1)
    )


def _response_summary(response) -> dict[str, Any]:
    vector_results = [item for item in response.results if item.vector_similarity is not None]
    best_similarity = max((float(item.vector_similarity) for item in vector_results), default=None)
    provenance_complete = bool(vector_results) and all(
        item.provenance.document_id
        and item.provenance.document_version_id
        and item.provenance.chunk_id
        and item.provenance.source_root
        and item.provenance.relative_path
        and item.provenance.start_line > 0
        and item.provenance.end_line >= item.provenance.start_line
        and item.provenance.content_hash
        and item.provenance.indexed_timestamp
        for item in vector_results
    )
    return {
        "mode": response.mode,
        "result_count": response.total,
        "vector_result_count": len(vector_results),
        "best_similarity": best_similarity,
        "provenance_complete": provenance_complete,
    }


def evaluate_probe_results(semantic, hybrid) -> dict[str, Any]:
    """Classify retrieval proof without relying on an LLM-generated statement."""

    semantic_summary = _response_summary(semantic)
    hybrid_summary = _response_summary(hybrid)
    passed = (
        semantic_summary["mode"] == "semantic"
        and hybrid_summary["mode"] == "hybrid"
        and semantic_summary["vector_result_count"] > 0
        and hybrid_summary["vector_result_count"] > 0
        and semantic_summary["provenance_complete"]
        and hybrid_summary["provenance_complete"]
    )
    return {
        "state": "verified" if passed else "failed",
        "semantic": semantic_summary,
        "hybrid": hybrid_summary,
    }


def run(embedder: Embedder | None = None) -> dict[str, Any]:
    settings = get_settings()
    snapshot_path = settings.runtime_dir / "embedding-recovery-validation.json"
    checked_at = datetime.now(timezone.utc).isoformat()
    owned_embedder = embedder is None
    try:
        if not settings.embedding_timeout_circuit_bypass:
            raise RuntimeError("GPU recovery probe requires timeout-circuit bypass")
        if embedder is None:
            embedder = get_embedder(settings, deterministic=False)
        with SessionLocal() as session:
            anchor = _anchor(session, settings.embedding_revision)
            if not anchor or not anchor.strip():
                result = {
                    "state": "failed",
                    "reason": "no_current_vector_anchor",
                    "checked_at": checked_at,
                    "embedding_revision": settings.embedding_revision,
                }
            else:
                # Query text stays in memory.  The snapshot records only a SHA-256
                # fingerprint so no source content becomes an operational log.
                query = anchor.strip()[:500]
                request = SearchRequest(
                    query=query,
                    mode="semantic",
                    top_k=3,
                    embedding_revision=settings.embedding_revision,
                    minimum_similarity=0.0,
                )
                semantic = search(session, request, settings, embedder)
                hybrid = search(
                    session,
                    request.model_copy(update={"mode": "hybrid"}),
                    settings,
                    embedder,
                )
                session.commit()
                result = {
                    **evaluate_probe_results(semantic, hybrid),
                    "checked_at": checked_at,
                    "embedding_revision": settings.embedding_revision,
                    "anchor_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                }
    except Exception as exc:  # Fail closed and preserve only the class, not source/error text.
        result = {
            "state": "failed",
            "reason": "probe_exception",
            "error_type": type(exc).__name__,
            "checked_at": checked_at,
            "embedding_revision": settings.embedding_revision,
        }
    finally:
        if owned_embedder and embedder is not None:
            embedder.close()
    write_snapshot(snapshot_path, result)
    return result


def main() -> None:
    result = run()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["state"] != "verified":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
