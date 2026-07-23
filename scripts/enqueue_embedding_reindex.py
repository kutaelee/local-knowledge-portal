import hashlib
import json

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
from lkp_indexer.queue import enqueue
from sqlalchemy import exists, select


def main() -> None:
    settings = get_settings()
    queued = 0
    with SessionLocal() as session:
        documents = session.scalars(
            select(Document)
            .join(SourceRoot, SourceRoot.id == Document.source_root_id)
            .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
            .where(
                Document.state == DocumentState.active,
                SourceRoot.data_scope == "production",
                exists(
                    select(DocumentChunk.id).where(
                        DocumentChunk.document_version_id == DocumentVersion.id,
                        ~exists(
                            select(ChunkEmbedding.id).where(
                                ChunkEmbedding.chunk_id == DocumentChunk.id,
                                ChunkEmbedding.embedding_revision
                                == settings.embedding_revision,
                            )
                        ),
                    )
                ),
            )
        ).all()
        for document in documents:
            key_material = (
                f"reindex:{settings.embedding_revision}:"
                f"{document.id}:{document.current_content_hash}"
            )
            job = enqueue(
                session,
                key=hashlib.sha256(key_material.encode()).hexdigest(),
                source_root_id=document.source_root_id,
                canonical_path=document.canonical_path,
                job_type="embedding_reindex",
                details={"embedding_revision": settings.embedding_revision},
                priority=10,
            )
            queued += int(job is not None)
        session.commit()
    print(
        json.dumps(
            {
                "embedding_revision": settings.embedding_revision,
                "documents_without_revision": len(documents),
                "jobs_queued": queued,
            }
        )
    )


if __name__ == "__main__":
    main()
