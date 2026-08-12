"""Apply a semantic-only policy change without mutating project classification.

Embeddings are rebuildable derived data. This utility deliberately leaves
documents, versions, chunks, project keys and source files intact; it only
removes vectors that a newly configured source-root semantic policy excludes.
"""

import argparse
import json
import os
from collections import Counter
from collections.abc import Callable
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
from lkp.settings import get_settings
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .selection import SEMANTIC_POLICY_VERSION, semantic_policy


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def migrate_semantic_policy(
    *,
    apply: bool,
    repository_mode: str,
    session_factory: Callable[[], Session] = SessionLocal,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    """Plan or apply a derived-vector cleanup for current semantic policy."""

    with session_factory() as session:
        rows = session.execute(
            select(Document, SourceRoot)
            .join(SourceRoot, SourceRoot.id == Document.source_root_id)
            .where(Document.state == DocumentState.active)
            .order_by(Document.canonical_path)
        ).all()
        excluded: list[tuple[Document, str]] = []
        reasons: Counter[str] = Counter()
        missing_sources: list[str] = []
        for document, root in rows:
            path = Path(document.canonical_path)
            if not path.exists():
                missing_sources.append(document.canonical_path)
                continue
            allowed, reason = semantic_policy(
                path,
                root,
                repository_mode=repository_mode,
            )
            if not allowed:
                normalized_reason = reason or "semantic_policy"
                excluded.append((document, normalized_reason))
                reasons[normalized_reason] += 1

        reason_by_document_id = {document.id: reason for document, reason in excluded}
        excluded_ids = list(reason_by_document_id)
        version_ids = list(
            session.scalars(
                select(DocumentVersion.id).where(DocumentVersion.document_id.in_(excluded_ids))
            )
        ) if excluded_ids else []
        chunk_ids = list(
            session.scalars(
                select(DocumentChunk.id).where(DocumentChunk.document_version_id.in_(version_ids))
            )
        ) if version_ids else []
        embedding_count = int(
            session.scalar(
                select(func.count())
                .select_from(ChunkEmbedding)
                .where(ChunkEmbedding.chunk_id.in_(chunk_ids))
            )
            or 0
        ) if chunk_ids else 0
        result: dict[str, Any] = {
            "policy": SEMANTIC_POLICY_VERSION,
            "status": "planned",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "repository_embedding_mode": repository_mode,
            "documents_examined": len(rows),
            "semantic_excluded_documents": len(excluded_ids),
            "semantic_exclusion_counts": dict(sorted(reasons.items())),
            "embeddings_to_remove": embedding_count,
            "missing_source_file_count": len(missing_sources),
            "source_files_deleted": 0,
            "project_keys_changed": 0,
            "chunks_deleted": 0,
        }
        if not apply:
            return result

        if chunk_ids:
            session.execute(delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(chunk_ids)))
        if version_ids:
            versions = session.scalars(
                select(DocumentVersion).where(DocumentVersion.id.in_(version_ids))
            ).all()
            for version in versions:
                version.metadata_json = {
                    **(version.metadata_json or {}),
                    "embedding_status": "skipped_policy",
                    "embedding_skip_reason": reason_by_document_id[version.document_id],
                    "embedding_revision": None,
                    "embedding_policy": SEMANTIC_POLICY_VERSION,
                }
        session.commit()
        result["status"] = "completed"
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

    if manifest_path is not None:
        _atomic_json(manifest_path, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    result = migrate_semantic_policy(
        apply=args.apply,
        repository_mode=settings.repository_embedding_mode,
        manifest_path=args.manifest,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
