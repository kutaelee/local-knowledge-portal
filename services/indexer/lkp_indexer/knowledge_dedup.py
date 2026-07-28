"""GPU-admitted semantic duplicate checks for reusable knowledge candidates.

The hook collector never calls a model.  It records deterministic key terms and
leaves new canonical publication pending.  This worker runs only inside the
existing gpuq-controlled embedding batch, performs a key-term prefilter, then
uses pgvector cosine similarity against rebuildable candidate/case vectors.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from lkp.db import SessionLocal
from lkp.models import KnowledgeCandidate, KnowledgeCase, KnowledgeSimilarityEmbedding
from lkp.settings import Settings, get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from .embedding import OllamaEmbedder, RateLimitedEmbedder
from .knowledge import knowledge_key_terms, publish_candidate
from .service_runtime import assert_mount_guards, service_pid

_POLICY = "key_terms_then_pgvector-v1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _candidate_text(row: KnowledgeCandidate) -> str:
    return "\n".join([row.problem, row.symptom, row.root_cause, row.solution])[:16_000]


def _case_text(row: KnowledgeCase) -> str:
    return "\n".join([row.problem, row.symptom, row.root_cause, row.solution])[:16_000]


def _metadata_terms(row: KnowledgeCandidate) -> list[str]:
    metadata = dict(row.metadata_json or {})
    semantic = metadata.get("semantic_dedup") or {}
    values = semantic.get("key_terms") if isinstance(semantic, dict) else None
    if isinstance(values, list) and all(isinstance(value, str) for value in values):
        return values[:24]
    return knowledge_key_terms(row.problem, row.root_cause, row.solution)


def _case_terms(row: KnowledgeCase) -> list[str]:
    metadata = dict(row.metadata_json or {})
    semantic = metadata.get("semantic_dedup") or {}
    values = semantic.get("key_terms") if isinstance(semantic, dict) else None
    if isinstance(values, list) and all(isinstance(value, str) for value in values):
        return values[:24]
    return knowledge_key_terms(row.problem, row.root_cause, row.solution)


def _ensure_embeddings(
    session: Session,
    *,
    rows: list[tuple[str, Any, str, list[str]]],
    embedder: RateLimitedEmbedder,
    settings: Settings,
) -> dict[tuple[str, str], KnowledgeSimilarityEmbedding]:
    """Persist only rebuildable vectors whose content/revision changed."""

    cached: dict[tuple[str, str], KnowledgeSimilarityEmbedding] = {}
    pending: list[tuple[str, Any, str, list[str], str]] = []
    for record_type, row, value, terms in rows:
        content_hash = _content_hash(value)
        existing = session.scalar(
            select(KnowledgeSimilarityEmbedding).where(
                KnowledgeSimilarityEmbedding.record_type == record_type,
                KnowledgeSimilarityEmbedding.record_id == row.id,
                KnowledgeSimilarityEmbedding.embedding_revision == settings.embedding_revision,
            )
        )
        if existing is not None and existing.content_hash == content_hash:
            cached[(record_type, str(row.id))] = existing
            continue
        pending.append((record_type, row, value, terms, content_hash))

    if not pending:
        return cached
    vectors = embedder.embed([value for _, _, value, _, _ in pending])
    if len(vectors) != len(pending):
        raise RuntimeError("knowledge dedup embedding cardinality mismatch")
    now = _utcnow()
    for (record_type, row, _, terms, content_hash), vector in zip(pending, vectors, strict=True):
        if len(vector) != settings.embedding_dimension:
            raise RuntimeError("knowledge dedup dimension mismatch; stopped fail-closed")
        existing = session.scalar(
            select(KnowledgeSimilarityEmbedding).where(
                KnowledgeSimilarityEmbedding.record_type == record_type,
                KnowledgeSimilarityEmbedding.record_id == row.id,
                KnowledgeSimilarityEmbedding.embedding_revision == settings.embedding_revision,
            )
        )
        if existing is None:
            existing = KnowledgeSimilarityEmbedding(
                record_type=record_type,
                record_id=row.id,
                content_hash=content_hash,
                key_terms=terms,
                embedding_revision=settings.embedding_revision,
                provider=embedder.provider,
                model=embedder.model,
                model_digest=embedder.digest,
                dimension=embedder.dimension,
                embedding=vector,
                created_at=now,
                updated_at=now,
            )
            session.add(existing)
        else:
            existing.content_hash = content_hash
            existing.key_terms = terms
            existing.provider = embedder.provider
            existing.model = embedder.model
            existing.model_digest = embedder.digest
            existing.dimension = embedder.dimension
            existing.embedding = vector
            existing.updated_at = now
        session.flush()
        cached[(record_type, str(row.id))] = existing
    return cached


def run_once(
    session: Session,
    settings: Settings,
    embedder: RateLimitedEmbedder,
) -> dict[str, int]:
    """Check pending candidates and publish only explicit non-duplicate passes."""

    blocked_rows = list(
        session.scalars(
            select(KnowledgeCandidate).where(
                KnowledgeCandidate.evidence_gate_status != "VERIFIED",
                KnowledgeCandidate.metadata_json["semantic_dedup"]["state"].astext
                == "pending_gpu_vector_check",
            )
        )
    )
    for candidate in blocked_rows:
        metadata = dict(candidate.metadata_json or {})
        semantic = dict(metadata.get("semantic_dedup") or {})
        semantic["state"] = "blocked_by_evidence_gate"
        metadata["semantic_dedup"] = semantic
        candidate.metadata_json = metadata
        candidate.updated_at = _utcnow()
    session.flush()

    candidates = list(
        session.scalars(
            select(KnowledgeCandidate)
            .where(
                KnowledgeCandidate.status.in_(["candidate", "verified"]),
                KnowledgeCandidate.evidence_gate_status == "VERIFIED",
                KnowledgeCandidate.metadata_json["semantic_dedup"]["state"].astext
                == "pending_gpu_vector_check",
            )
            .order_by(KnowledgeCandidate.updated_at, KnowledgeCandidate.id)
            .limit(settings.knowledge_dedup_max_candidates_per_run)
        )
    )
    pending = candidates
    cases = list(
        session.scalars(
            select(KnowledgeCase).where(KnowledgeCase.status == "verified")
        )
    )
    result = {
        "considered": len(pending),
        "blocked_by_evidence": len(blocked_rows),
        "passed": 0,
        "needs_review": 0,
        "published": 0,
    }

    for candidate in pending:
        terms = _metadata_terms(candidate)
        term_set = set(terms)
        # Key terms are a scope filter, not a duplicate decision.  This keeps a
        # long-lived case corpus from doing an unbounded all-to-all vector scan.
        scoped_cases = [
            row for row in cases if term_set.intersection(_case_terms(row))
        ]
        metadata = dict(candidate.metadata_json or {})
        semantic = dict(metadata.get("semantic_dedup") or {})
        semantic.update(
            {"policy": _POLICY, "key_terms": terms, "checked_at": _utcnow().isoformat()}
        )
        if not scoped_cases:
            semantic.update({"state": "verified_no_keyterm_candidates", "matches": []})
            metadata["semantic_dedup"] = semantic
            candidate.metadata_json = metadata
            candidate.updated_at = _utcnow()
            result["passed"] += 1
        else:
            embeddings = _ensure_embeddings(
                session,
                rows=[
                    ("candidate", candidate, _candidate_text(candidate), terms),
                    *[("case", row, _case_text(row), _case_terms(row)) for row in scoped_cases],
                ],
                embedder=embedder,
                settings=settings,
            )
            candidate_embedding = embeddings[("candidate", str(candidate.id))]
            distance = KnowledgeSimilarityEmbedding.embedding.cosine_distance(
                candidate_embedding.embedding
            ).label("cosine_distance")
            matches = session.execute(
                select(KnowledgeSimilarityEmbedding, KnowledgeCase, distance)
                .join(KnowledgeCase, KnowledgeCase.id == KnowledgeSimilarityEmbedding.record_id)
                .where(
                    KnowledgeSimilarityEmbedding.record_type == "case",
                    KnowledgeSimilarityEmbedding.embedding_revision == settings.embedding_revision,
                    KnowledgeSimilarityEmbedding.key_terms.overlap(terms),
                    KnowledgeCase.status == "verified",
                )
                .order_by(distance)
                .limit(5)
            ).all()
            safe_matches = [
                {
                    "case_id": str(row_case.id),
                    "similarity": round(1 - float(row_distance), 6),
                }
                for _, row_case, row_distance in matches
            ]
            safe_matches.sort(key=lambda item: item["similarity"], reverse=True)
            high = [
                item
                for item in safe_matches
                if item["similarity"] >= settings.knowledge_dedup_similarity_threshold
            ]
            if high:
                semantic.update({"state": "needs_review", "matches": high})
                candidate.status = "needs_review"
                candidate.evidence_gate_status = "NEEDS_REVIEW"
                candidate.metadata_json = {
                    **metadata,
                    "semantic_dedup": semantic,
                    "possible_duplicate_case_id": high[0]["case_id"],
                }
                candidate.updated_at = _utcnow()
                result["needs_review"] += 1
                continue
            semantic.update({"state": "verified_no_semantic_duplicate", "matches": safe_matches})
            metadata["semantic_dedup"] = semantic
            candidate.metadata_json = metadata
            candidate.updated_at = _utcnow()
            result["passed"] += 1

        if (
            bool((candidate.metadata_json or {}).get("auto_publish_eligible"))
            and settings.knowledge_auto_publish
        ):
            _, outcome = publish_candidate(session, candidate)
            result["published"] += int(
                outcome in {"CREATED_CANONICAL", "MERGED_OCCURRENCE", "REVISED_CANONICAL"}
            )
    session.flush()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.once:
        parser.error("only --once is supported; scheduling belongs to gpuq")
    settings = get_settings()
    if not settings.embedding_timeout_circuit_bypass:
        raise SystemExit(
            "knowledge dedup requires the gpuq embedding batch (circuit bypass is absent)"
        )
    assert_mount_guards(settings)
    embedder = RateLimitedEmbedder(
        OllamaEmbedder(
            settings.ollama_base_url,
            settings.embedding_model,
            settings.embedding_model_digest,
            settings.embedding_dimension,
            timeout_seconds=settings.embedding_request_timeout_seconds,
            keep_alive=settings.embedding_keep_alive,
        ),
        batch_size=settings.embedding_batch_size,
        cooldown_seconds=settings.embedding_batch_cooldown_seconds,
    )
    try:
        with service_pid():
            with SessionLocal() as session:
                result = run_once(session, settings, embedder)
                session.commit()
    finally:
        embedder.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
