import hashlib
import mimetypes
import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path

from lkp.models import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentState,
    DocumentVersion,
    IngestEvent,
    IngestJob,
    JobStatus,
    SourceRoot,
    WorkerHeartbeat,
)
from lkp.settings import Settings
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .chunking import chunk_document
from .embedding import Embedder
from .paths import canonicalize
from .queue import fail, finish


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def heartbeat(
    session: Session,
    worker_id: str,
    state: str,
    job_id: uuid.UUID | None = None,
    *,
    success: bool = False,
    failure: bool = False,
) -> None:
    row = session.get(WorkerHeartbeat, worker_id)
    if row is None:
        row = WorkerHeartbeat(
            worker_id=worker_id,
            hostname=socket.gethostname(),
            process_id=os.getpid(),
            version="0.1.0",
            state=state,
            current_job_id=job_id,
            processed_count=0,
            failed_count=0,
        )
        session.add(row)
    row.last_seen_at = _utcnow()
    row.state = state
    row.current_job_id = job_id
    row.processed_count += int(success)
    row.failed_count += int(failure)


def _embed_missing(
    session: Session,
    version: DocumentVersion,
    settings: Settings,
    embedder: Embedder,
) -> None:
    chunks = list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_version_id == version.id)
            .order_by(DocumentChunk.chunk_index)
        )
    )
    if not chunks:
        return
    existing_chunk_ids = set(
        session.scalars(
            select(ChunkEmbedding.chunk_id).where(
                ChunkEmbedding.embedding_revision == settings.embedding_revision,
                ChunkEmbedding.chunk_id.in_([chunk.id for chunk in chunks]),
            )
        )
    )
    missing = [chunk for chunk in chunks if chunk.id not in existing_chunk_ids]
    if not missing:
        return
    vectors = embedder.embed([chunk.content for chunk in missing])
    if any(len(vector) != settings.embedding_dimension for vector in vectors):
        raise RuntimeError("embedding dimension mismatch; pipeline stopped fail-closed")
    for chunk, vector in zip(missing, vectors, strict=True):
        session.add(
            ChunkEmbedding(
                chunk_id=chunk.id,
                embedding_revision=settings.embedding_revision,
                provider=embedder.provider,
                model=embedder.model,
                model_digest=embedder.digest,
                dimension=embedder.dimension,
                embedding=vector,
            )
        )
    version.metadata_json = {
        **version.metadata_json,
        "embedding_revision": settings.embedding_revision,
        "embedding_status": "complete",
        "indexed_at": _utcnow().isoformat(),
    }


def process_job(
    session: Session,
    job: IngestJob,
    settings: Settings,
    embedder: Embedder | None,
    worker_id: str,
) -> None:
    heartbeat(session, worker_id, "busy", job.id)
    job.status = JobStatus.processing
    session.flush()
    try:
        root = session.get(SourceRoot, job.source_root_id)
        if root is None or not root.enabled:
            raise RuntimeError("source root missing or disabled")
        root_path = Path(root.canonical_path)
        path = Path(job.canonical_path)
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"{root.id}:{str(path).casefold()}"},
        )
        existing = session.scalar(
            select(Document).where(
                Document.source_root_id == root.id,
                Document.canonical_path == str(path),
            )
        )
        rename_from = (job.error_details or {}).get("rename_from")
        if rename_from and existing is None:
            renamed = session.scalar(
                select(Document).where(
                    Document.source_root_id == root.id,
                    Document.canonical_path == rename_from,
                )
            )
            if renamed:
                renamed.canonical_path = str(path)
                renamed.relative_path = path.relative_to(root_path).as_posix()
                renamed.filename = path.name
                renamed.extension = path.suffix.lower()
                renamed.parent_path = path.parent.relative_to(root_path).as_posix()
                existing = renamed
                session.add(
                    IngestEvent(
                        source_root_id=root.id,
                        document_id=renamed.id,
                        event_type="renamed",
                        path=str(path),
                        details={"from": rename_from, "to": str(path)},
                    )
                )
        if not path.exists():
            if existing:
                existing.state = DocumentState.deleted
                existing.last_seen_at = _utcnow()
                job.document_id = existing.id
                session.add(
                    IngestEvent(
                        source_root_id=root.id,
                        document_id=existing.id,
                        event_type="deleted",
                        path=str(path),
                    )
                )
            finish(session, job)
            heartbeat(session, worker_id, "idle", success=True)
            return
        canonical = canonicalize(path, root_path)
        info = canonical.stat()
        if info.st_size > settings.max_file_bytes:
            raise ValueError(f"file exceeds {settings.max_file_bytes} bytes")
        raw = canonical.read_bytes()
        if b"\x00" in raw[:8192]:
            raise ValueError("binary file rejected")
        content = raw.decode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        relative = canonical.relative_to(root_path).as_posix()
        modified = datetime.fromtimestamp(info.st_mtime, tz=timezone.utc)
        if existing is None:
            project_key = relative.split("/", 1)[0] if "/" in relative else root.name
            existing = Document(
                source_root_id=root.id,
                canonical_path=str(canonical),
                relative_path=relative,
                filename=canonical.name,
                extension=canonical.suffix.lower(),
                mime_type=mimetypes.guess_type(canonical.name)[0] or "text/plain",
                project_key=project_key,
                parent_path=canonical.parent.relative_to(root_path).as_posix(),
                size_bytes=info.st_size,
                modified_at_fs=modified,
                state=DocumentState.active,
            )
            session.add(existing)
            session.flush()
            change_type = "created"
        else:
            change_type = "restored" if existing.state == DocumentState.deleted else "modified"
        job.document_id = existing.id
        existing.last_seen_at = _utcnow()
        existing.modified_at_fs = modified
        existing.size_bytes = info.st_size
        existing.state = DocumentState.active
        if existing.current_content_hash == digest:
            if embedder and existing.current_version_id:
                current_version = session.get(DocumentVersion, existing.current_version_id)
                if current_version:
                    _embed_missing(session, current_version, settings, embedder)
            finish(session, job)
            heartbeat(session, worker_id, "idle", success=True)
            return
        duplicate = session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == existing.id,
                DocumentVersion.content_hash == digest,
            )
        )
        if duplicate:
            existing.current_content_hash = digest
            existing.current_version_id = duplicate.id
            if embedder:
                _embed_missing(session, duplicate, settings, embedder)
            finish(session, job)
            heartbeat(session, worker_id, "idle", success=True)
            return
        chunks, parsed_metadata = chunk_document(canonical, content)
        vectors = embedder.embed([chunk.content for chunk in chunks]) if embedder and chunks else []
        if embedder and any(len(vector) != settings.embedding_dimension for vector in vectors):
            raise RuntimeError("embedding dimension mismatch; pipeline stopped fail-closed")
        version = DocumentVersion(
            document_id=existing.id,
            content_hash=digest,
            byte_size=info.st_size,
            modified_at_fs=modified,
            parser_version=settings.parser_version,
            chunker_version=settings.chunker_version,
            line_count=len(content.splitlines()),
            metadata_json={
                **parsed_metadata,
                "pipeline_version": settings.pipeline_version,
                "embedding_revision": settings.embedding_revision if embedder else None,
                "embedding_status": "complete" if embedder else "pending",
                "indexed_at": _utcnow().isoformat(),
            },
            previous_version_id=existing.current_version_id,
            change_type=change_type,
            diff_summary={"previous_hash": existing.current_content_hash},
        )
        session.add(version)
        session.flush()
        for index, parsed in enumerate(chunks):
            chunk = DocumentChunk(
                document_version_id=version.id,
                chunk_index=parsed.index,
                chunk_type=parsed.kind,
                heading_path=parsed.heading_path,
                symbol_name=parsed.symbol_name,
                language=parsed.language,
                start_line=parsed.start_line,
                end_line=parsed.end_line,
                content=parsed.content,
                content_hash=parsed.content_hash,
                token_estimate=max(1, len(parsed.content) // 4),
                metadata_json=parsed.metadata,
            )
            session.add(chunk)
            session.flush()
            if embedder:
                session.add(
                    ChunkEmbedding(
                        chunk_id=chunk.id,
                        embedding_revision=settings.embedding_revision,
                        provider=embedder.provider,
                        model=embedder.model,
                        model_digest=embedder.digest,
                        dimension=embedder.dimension,
                        embedding=vectors[index],
                    )
                )
        existing.current_content_hash = digest
        existing.current_version_id = version.id
        session.add(
            IngestEvent(
                source_root_id=root.id,
                document_id=existing.id,
                event_type="indexed",
                path=str(canonical),
                details={"version_id": str(version.id), "chunks": len(chunks)},
            )
        )
        finish(session, job)
        heartbeat(session, worker_id, "idle", success=True)
    except Exception as exc:
        fail(session, job, exc)
        heartbeat(session, worker_id, "error", failure=True)
        raise
