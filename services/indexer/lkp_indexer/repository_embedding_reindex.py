"""Build retrieval embeddings for current repository knowledge snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone

from lkp.db import SessionLocal
from lkp.settings import get_settings
from sqlalchemy import text

from .cli import get_embedder
from .repository_reference_policy import current_references_sql, latest_snapshot_sql

_CURRENT_REPOSITORY_REFERENCES = current_references_sql("k")
_LATEST_REPOSITORY_SNAPSHOT = latest_snapshot_sql("s")


def embedding_text(item: dict) -> str:
    sections = [
        f"제목: {item['title']}",
        f"지식 유형: {item['knowledge_type']}",
        f"요약: {item['summary']}",
        f"상세: {item['detail']}",
        f"처리 절차: {' / '.join(item['processing_steps'] or [])}",
        f"구성요소: {', '.join(item['components'] or [])}",
        f"설정: {', '.join(item['configurations'] or [])}",
        f"의존 항목: {', '.join(item['dependencies'] or [])}",
        f"검증 상태: {item['validation_status']}",
        f"확인 필요: {' / '.join(item['unknowns'] or [])}",
    ]
    return "\n".join(section for section in sections if not section.endswith(": "))


def run(limit: int | None = None) -> dict[str, object]:
    settings = get_settings()
    if not settings.embedding_timeout_circuit_bypass:
        raise RuntimeError("GPU reindex requires LKP_EMBEDDING_TIMEOUT_CIRCUIT_BYPASS=true")
    if settings.embedding_dimension != 1024:
        raise RuntimeError("repository knowledge embedding schema requires dimension 1024")
    embedder = get_embedder(settings, deterministic=False)
    try:
        result = run_with_embedder(settings, embedder, limit)
    finally:
        embedder.close()
    metrics = getattr(embedder, "performance_metrics", None)
    result["model_performance"] = metrics() if metrics is not None else None
    return result


def run_with_embedder(settings, embedder, limit: int | None) -> dict[str, object]:
    result: dict[str, object] = {
        "examined": 0,
        "embedded": 0,
        "skipped": 0,
        "pruned_stale": 0,
        "pruned_ineligible": 0,
        "pruned_invalid_references": 0,
    }
    with SessionLocal() as session:
        pruned = session.execute(
            text(
                f"""
                DELETE FROM repository_knowledge_embedding e
                USING repository_knowledge_item k, repository_snapshot s
                WHERE e.knowledge_item_id = k.id
                  AND k.snapshot_id = s.id
                  AND NOT ({_LATEST_REPOSITORY_SNAPSHOT})
                """
            )
        )
        result["pruned_stale"] = max(0, int(pruned.rowcount or 0))
        pruned = session.execute(
            text(
                """
                DELETE FROM repository_knowledge_embedding e
                USING repository_knowledge_item k
                WHERE e.knowledge_item_id = k.id
                  AND (
                    k.searchable = false OR
                    k.validation_status IN ('REJECTED', 'STALE')
                  )
                """
            )
        )
        result["pruned_ineligible"] = max(0, int(pruned.rowcount or 0))
        pruned = session.execute(
            text(
                f"""
                DELETE FROM repository_knowledge_embedding e
                USING repository_knowledge_item k
                WHERE e.knowledge_item_id = k.id
                  AND NOT ({_CURRENT_REPOSITORY_REFERENCES})
                """
            )
        )
        result["pruned_invalid_references"] = max(0, int(pruned.rowcount or 0))
        session.commit()
        statement = f"""
            SELECT k.id, k.knowledge_type, k.title, k.summary, k.detail,
                   k.processing_steps, k.components, k.configurations,
                   k.dependencies, k.validation_status, k.unknowns
            FROM repository_knowledge_item k
            JOIN repository_snapshot s ON s.id = k.snapshot_id
            LEFT JOIN repository_knowledge_embedding e
              ON e.knowledge_item_id = k.id
             AND e.embedding_revision = :revision
            WHERE k.searchable = true
              AND k.validation_status != 'REJECTED'
              AND {_LATEST_REPOSITORY_SNAPSHOT}
              AND {_CURRENT_REPOSITORY_REFERENCES}
              AND e.id IS NULL
            ORDER BY k.created_at, k.id
        """
        if limit is not None:
            statement += " LIMIT :limit"
        rows = [
            dict(row)
            for row in session.execute(
                text(statement),
                {"revision": settings.embedding_revision, "limit": limit},
            ).mappings()
        ]
        provider_batch_size = max(1, int(getattr(embedder, "batch_size", 1)))
        for offset in range(0, len(rows), provider_batch_size):
            batch = rows[offset : offset + provider_batch_size]
            values = [embedding_text(item)[: settings.embedding_input_max_chars] for item in batch]
            vectors = embedder.embed(values)
            for item, value, vector in zip(batch, values, vectors, strict=True):
                result["examined"] = int(result["examined"]) + 1
                digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
                session.execute(
                    text(
                        """
                        INSERT INTO repository_knowledge_embedding (
                          id, knowledge_item_id, embedding_text_hash,
                          embedding_revision, provider, model, model_digest,
                          dimension, embedding, created_at
                        ) VALUES (
                          :id, :knowledge_item_id, :embedding_text_hash,
                          :embedding_revision, :provider, :model, :model_digest,
                          :dimension, CAST(:embedding AS vector), :created_at
                        )
                        ON CONFLICT (knowledge_item_id, embedding_revision)
                        DO NOTHING
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "knowledge_item_id": item["id"],
                        "embedding_text_hash": digest,
                        "embedding_revision": settings.embedding_revision,
                        "provider": embedder.provider,
                        "model": embedder.model,
                        "model_digest": embedder.digest,
                        "dimension": embedder.dimension,
                        "embedding": json.dumps(vector),
                        "created_at": datetime.now(timezone.utc),
                    },
                )
                result["embedded"] = int(result["embedded"]) + 1
            session.commit()
    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    metrics = getattr(embedder, "performance_metrics", None)
    result["model_performance"] = metrics() if metrics is not None else None
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        raise SystemExit("--limit must be positive")
    print(json.dumps(run(args.limit), ensure_ascii=False))


if __name__ == "__main__":
    main()
