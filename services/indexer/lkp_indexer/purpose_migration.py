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

from .projects import project_identity
from .selection import semantic_policy


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def migrate_purpose_scope(
    *,
    apply: bool,
    repository_mode: str,
    session_factory: Callable[[], Session] = SessionLocal,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    with session_factory() as session:
        rows = session.execute(
            select(Document, SourceRoot)
            .join(SourceRoot, SourceRoot.id == Document.source_root_id)
            .where(Document.state == DocumentState.active)
            .order_by(Document.canonical_path)
        ).all()
        project_changes: list[dict[str, str]] = []
        project_change_counts: Counter[str] = Counter()
        semantic_excluded: list[tuple[Document, str]] = []
        semantic_exclusion_counts: Counter[str] = Counter()
        missing_sources: list[str] = []
        for document, root in rows:
            path = Path(document.canonical_path)
            if not path.exists():
                missing_sources.append(document.canonical_path)
                continue
            identity = project_identity(path, Path(root.canonical_path))
            if (
                document.project_key != identity.key
                or document.project_relative_path != identity.relative_path
            ):
                project_changes.append(
                    {
                        "document_id": str(document.id),
                        "from": document.project_key or "",
                        "to": identity.key,
                    }
                )
                project_change_counts[
                    f"{document.project_key or '(unset)'} -> {identity.key}"
                ] += 1
                if apply:
                    document.project_key = identity.key
                    document.project_relative_path = identity.relative_path
            allowed, reason = semantic_policy(
                path,
                root,
                repository_mode=repository_mode,
            )
            if not allowed:
                exclusion_reason = reason or "semantic_policy"
                semantic_excluded.append((document, exclusion_reason))
                semantic_exclusion_counts[exclusion_reason] += 1

        exclusion_reason_by_document_id = {
            document.id: reason for document, reason in semantic_excluded
        }
        excluded_ids = list(exclusion_reason_by_document_id)
        version_ids = list(
            session.scalars(
                select(DocumentVersion.id).where(
                    DocumentVersion.document_id.in_(excluded_ids)
                )
            )
        ) if excluded_ids else []
        chunk_ids = list(
            session.scalars(
                select(DocumentChunk.id).where(
                    DocumentChunk.document_version_id.in_(version_ids)
                )
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
            "policy": "purpose-aware-v2",
            "status": "planned",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "repository_embedding_mode": repository_mode,
            "documents_examined": len(rows),
            "project_documents_reclassified": len(project_changes),
            "project_change_counts": dict(sorted(project_change_counts.items())),
            "project_changes_sample": project_changes[:50],
            "semantic_excluded_documents": len(excluded_ids),
            "semantic_exclusion_counts": dict(
                sorted(semantic_exclusion_counts.items())
            ),
            "embeddings_to_remove": embedding_count,
            "missing_source_file_count": len(missing_sources),
            "missing_source_files_sample": missing_sources[:50],
            "source_files_deleted": 0,
        }
        if not apply:
            return result

        if chunk_ids:
            session.execute(
                delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(chunk_ids))
            )
        if version_ids:
            versions = session.scalars(
                select(DocumentVersion).where(DocumentVersion.id.in_(version_ids))
            ).all()
            for version in versions:
                version.metadata_json = {
                    **(version.metadata_json or {}),
                    "embedding_status": "skipped_policy",
                    "embedding_skip_reason": exclusion_reason_by_document_id[
                        version.document_id
                    ],
                    "embedding_revision": None,
                    "embedding_policy": "purpose-aware-v2",
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
    result = migrate_purpose_scope(
        apply=args.apply,
        repository_mode=settings.repository_embedding_mode,
        manifest_path=args.manifest,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
