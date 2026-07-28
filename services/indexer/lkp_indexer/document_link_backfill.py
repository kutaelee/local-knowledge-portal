from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from lkp.models import Document, DocumentState, SourceRoot
from sqlalchemy import select
from sqlalchemy.orm import Session

from .document_links import build_document_catalog, sync_document_links
from .file_safety import source_file_rejection_reason
from .paths import UnsafePathError, canonicalize


def backfill_document_links(
    session: Session,
    *,
    max_file_bytes: int,
) -> dict[str, int]:
    """Rebuild derived Markdown relations without enqueuing embeddings.

    Source files remain read-only. Each file is revalidated against its
    registered source root immediately before reading.
    """

    rows = list(
        session.scalars(
            select(Document)
            .where(
                Document.state == DocumentState.active,
                Document.extension.in_((".md", ".mdx")),
            )
            .order_by(Document.source_root_id, Document.id)
        )
    )
    grouped: dict[object, list[Document]] = defaultdict(list)
    for row in rows:
        grouped[row.source_root_id].append(row)

    result = {
        "documents_considered": len(rows),
        "documents_synced": 0,
        "links_extracted": 0,
        "missing_files": 0,
        "unsafe_paths": 0,
        "unsupported_files": 0,
        "invalid_utf8": 0,
    }
    for root_id, documents in grouped.items():
        root = session.get(SourceRoot, root_id)
        if root is None:
            result["unsafe_paths"] += len(documents)
            continue
        root_path = Path(root.canonical_path)
        catalog = build_document_catalog(session, root_id)
        for document in documents:
            try:
                path = canonicalize(
                    Path(document.canonical_path),
                    root_path,
                    must_exist=True,
                )
            except FileNotFoundError:
                result["missing_files"] += 1
                continue
            except (OSError, UnsafePathError):
                result["unsafe_paths"] += 1
                continue
            if source_file_rejection_reason(path, max_file_bytes):
                result["unsupported_files"] += 1
                continue
            try:
                content = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                result["invalid_utf8"] += 1
                continue
            result["links_extracted"] += sync_document_links(
                session,
                document,
                content,
                catalog=catalog,
            )
            result["documents_synced"] += 1
    return result
