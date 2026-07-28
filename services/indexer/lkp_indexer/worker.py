import hashlib
import mimetypes
import os
import socket
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from lkp.models import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentLink,
    DocumentState,
    DocumentTag,
    DocumentVersion,
    IngestEvent,
    IngestJob,
    SourceRoot,
    Tag,
    WorkerHeartbeat,
)
from lkp.settings import Settings
from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from .chunking import chunk_document
from .document_links import resolve_links_for_target, sync_document_links
from .embedding import Embedder
from .embedding_runtime import timeout_circuit_reason
from .file_safety import source_file_rejection_reason
from .ignore import IgnoreRules, IncludeRules
from .paths import canonicalize
from .projects import project_identity
from .queue import fail, finish
from .selection import SEMANTIC_POLICY_VERSION, semantic_policy


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


LOW_VALUE_EMBEDDING_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "uv.lock",
    "poetry.lock",
    "cargo.lock",
    "go.sum",
}


def bounded_embedding_input(content: str, *, max_chars: int) -> tuple[str, bool]:
    """Produce a deterministic bounded representation without changing source chunks.

    Full chunk text remains in PostgreSQL for citations and lexical retrieval.
    The vector receives the beginning and end of an overlong chunk, including
    its heading/context, so a slow local model cannot hold a worker lease for
    minutes on one large Markdown section.
    """

    if len(content) <= max_chars:
        return content, False
    marker = "\n\n[… bounded embedding input …]\n\n"
    usable = max_chars - len(marker)
    if usable < 2:
        raise ValueError("embedding input max chars is too small")
    head = (usable * 3) // 4
    tail = usable - head
    return f"{content[:head]}{marker}{content[-tail:]}", True


def _frontmatter_tags(metadata: dict) -> list[str]:
    raw = metadata.get("tags") or []
    if isinstance(raw, str):
        raw = [raw]
    labels = metadata.get("knowledge_value_labels") or []
    if isinstance(labels, str):
        labels = [labels]
    values = [*raw, *labels]
    for key, prefix in (
        ("category", "case"),
        ("knowledge_value_tier", "knowledge-value"),
        ("lifecycle_status", "lifecycle"),
    ):
        if metadata.get(key):
            values.append(f"{prefix}:{metadata[key]}")
    project = metadata.get("project")
    if project:
        values.append(f"project:{project}")
    return sorted(
        {normalized for item in values if (normalized := str(item).strip().lower()[:200])}
    )


def _sync_document_tags(session: Session, document: Document, metadata: dict) -> None:
    session.execute(delete(DocumentTag).where(DocumentTag.document_id == document.id))
    for name in _frontmatter_tags(metadata):
        tag = session.scalar(select(Tag).where(Tag.name == name))
        if tag is None:
            tag = Tag(name=name)
            session.add(tag)
            session.flush()
        session.add(DocumentTag(document_id=document.id, tag_id=tag.id))


def embedding_cost_decision(
    chunks: list,
    *,
    max_chunks: int,
    max_chars: int,
    path: Path | None = None,
) -> tuple[bool, str | None]:
    if path is not None:
        name = path.name.casefold()
        if name in LOW_VALUE_EMBEDDING_NAMES or ".min." in name or ".generated." in name:
            return False, "low_value_artifact"
    if len(chunks) > max_chunks:
        return False, "chunk_limit"
    if sum(len(chunk.content) for chunk in chunks) > max_chars:
        return False, "character_limit"
    return True, None


def heartbeat(
    session: Session,
    worker_id: str,
    state: str,
    job_id: uuid.UUID | None = None,
    *,
    success: bool = False,
    failure: bool = False,
    metadata: dict | None = None,
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
    row.metadata_json = {**(row.metadata_json or {}), **(metadata or {})}


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
    document = session.get(Document, version.document_id)
    root = session.get(SourceRoot, document.source_root_id) if document else None
    if document and root:
        policy_allowed, policy_reason = semantic_policy(
            Path(document.canonical_path),
            root,
            repository_mode=settings.repository_embedding_mode,
        )
        if not policy_allowed:
            version.metadata_json = {
                **version.metadata_json,
                "embedding_status": "skipped_policy",
                "embedding_skip_reason": policy_reason,
                "embedding_policy": SEMANTIC_POLICY_VERSION,
                "embedding_chunk_count": len(chunks),
                "embedding_character_count": sum(len(chunk.content) for chunk in chunks),
            }
            return
    allowed, reason = embedding_cost_decision(
        chunks,
        max_chunks=settings.embedding_max_chunks_per_document,
        max_chars=settings.embedding_max_chars_per_document,
        path=Path(document.canonical_path) if document else None,
    )
    if not allowed:
        version.metadata_json = {
            **version.metadata_json,
            "embedding_status": "skipped_cost_limit",
            "embedding_skip_reason": reason,
            "embedding_chunk_count": len(chunks),
            "embedding_character_count": sum(len(chunk.content) for chunk in chunks),
        }
        return
    runtime_reason = timeout_circuit_reason(session, settings)
    if runtime_reason:
        version.metadata_json = {
            **version.metadata_json,
            "embedding_status": "deferred_runtime",
            "embedding_defer_reason": runtime_reason,
            "embedding_revision": None,
            "embedding_policy": SEMANTIC_POLICY_VERSION,
            "embedding_chunk_count": len(chunks),
            "embedding_character_count": sum(len(chunk.content) for chunk in chunks),
        }
        return
    embedding_inputs: list[str] = []
    truncated_chunks = 0
    for chunk in missing:
        embedding_input, truncated = bounded_embedding_input(
            chunk.content,
            max_chars=settings.embedding_input_max_chars,
        )
        embedding_inputs.append(embedding_input)
        truncated_chunks += int(truncated)
    try:
        vectors = embedder.embed(embedding_inputs)
    except httpx.TimeoutException:
        version.metadata_json = {
            **version.metadata_json,
            "embedding_status": "deferred_runtime",
            "embedding_defer_reason": "embedding_request_timeout",
            "embedding_revision": None,
            "embedding_policy": SEMANTIC_POLICY_VERSION,
            "embedding_input_max_chars": settings.embedding_input_max_chars,
            "embedding_input_truncated_chunks": truncated_chunks,
        }
        return
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
        "embedding_input_max_chars": settings.embedding_input_max_chars,
        "embedding_input_truncated_chunks": truncated_chunks,
        "indexed_at": _utcnow().isoformat(),
    }


def process_job(
    session: Session,
    job: IngestJob,
    settings: Settings,
    embedder: Embedder | None,
    worker_id: str,
) -> None:
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
                session.execute(
                    delete(DocumentLink).where(
                        DocumentLink.source_document_id == existing.id
                    )
                )
                session.execute(
                    update(DocumentLink)
                    .where(DocumentLink.target_document_id == existing.id)
                    .values(target_document_id=None)
                )
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
        relative = canonical.relative_to(root_path).as_posix()
        identity = project_identity(canonical, root_path)
        ignored_by_exclusion = IgnoreRules(
            root_path, root.exclude_patterns
        ).matches(relative)
        ignored_by_inclusion = not IncludeRules(root.include_patterns).matches(
            relative
        )
        if ignored_by_exclusion or ignored_by_inclusion:
            if existing:
                existing.state = DocumentState.ignored
                existing.last_seen_at = _utcnow()
                job.document_id = existing.id
            session.add(
                IngestEvent(
                    source_root_id=root.id,
                    document_id=existing.id if existing else None,
                    event_type="ignored",
                    path=str(canonical),
                    details={
                        "reason": (
                            "ignore_rule"
                            if ignored_by_exclusion
                            else "outside_include_patterns"
                        ),
                        "relative_path": relative,
                    },
                )
            )
            finish(session, job)
            heartbeat(session, worker_id, "idle", success=True)
            return
        rejection_reason = source_file_rejection_reason(canonical, settings.max_file_bytes)
        if rejection_reason:
            if existing:
                existing.state = DocumentState.unsupported
                existing.last_seen_at = _utcnow()
                job.document_id = existing.id
            session.add(
                IngestEvent(
                    source_root_id=root.id,
                    document_id=existing.id if existing else None,
                    event_type="unsupported",
                    path=str(canonical),
                    details={
                        "reason": rejection_reason,
                        "relative_path": relative,
                        "size_bytes": info.st_size,
                    },
                )
            )
            finish(session, job)
            heartbeat(session, worker_id, "idle", success=True)
            return
        raw = canonical.read_bytes()
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            if existing:
                existing.state = DocumentState.unsupported
                existing.last_seen_at = _utcnow()
                job.document_id = existing.id
            session.add(
                IngestEvent(
                    source_root_id=root.id,
                    document_id=existing.id if existing else None,
                    event_type="unsupported",
                    path=str(canonical),
                    details={
                        "reason": "invalid_utf8",
                        "relative_path": relative,
                        "size_bytes": info.st_size,
                    },
                )
            )
            finish(session, job)
            heartbeat(session, worker_id, "idle", success=True)
            return
        digest = hashlib.sha256(raw).hexdigest()
        modified = datetime.fromtimestamp(info.st_mtime, tz=timezone.utc)
        if existing is None:
            existing = Document(
                source_root_id=root.id,
                canonical_path=str(canonical),
                relative_path=relative,
                filename=canonical.name,
                extension=canonical.suffix.lower(),
                mime_type=mimetypes.guess_type(canonical.name)[0] or "text/plain",
                project_key=identity.key,
                project_relative_path=identity.relative_path,
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
        existing.last_seen_at = _utcnow()
        existing.modified_at_fs = modified
        existing.size_bytes = info.st_size
        existing.state = DocumentState.active
        existing.project_key = identity.key
        existing.project_relative_path = identity.relative_path
        chunks, parsed_metadata = chunk_document(canonical, content)
        managed_project = parsed_metadata.get("project")
        if (
            parsed_metadata.get("managed") is True
            and isinstance(managed_project, str)
            and managed_project.strip()
        ):
            existing.project_key = managed_project.strip()[:200]
        _sync_document_tags(session, existing, parsed_metadata)
        link_count = sync_document_links(session, existing, content)
        resolved_link_count = resolve_links_for_target(session, existing)
        if existing.current_content_hash == digest:
            if embedder and existing.current_version_id:
                current_version = session.get(DocumentVersion, existing.current_version_id)
                if current_version:
                    _embed_missing(session, current_version, settings, embedder)
            job.document_id = existing.id
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
            job.document_id = existing.id
            finish(session, job)
            heartbeat(session, worker_id, "idle", success=True)
            return
        policy_allowed, policy_reason = semantic_policy(
            canonical,
            root,
            repository_mode=settings.repository_embedding_mode,
        )
        cost_allowed, cost_reason = embedding_cost_decision(
            chunks,
            max_chunks=settings.embedding_max_chunks_per_document,
            max_chars=settings.embedding_max_chars_per_document,
            path=canonical,
        )
        embedding_allowed = policy_allowed and cost_allowed
        embedding_skip_reason = policy_reason if not policy_allowed else cost_reason
        runtime_reason = timeout_circuit_reason(session, settings)
        embedding_deferred = bool(embedding_allowed and runtime_reason)
        should_embed = bool(embedder and chunks and embedding_allowed and not runtime_reason)
        embedding_inputs: list[str] = []
        truncated_chunks = 0
        if should_embed:
            for chunk in chunks:
                embedding_input, truncated = bounded_embedding_input(
                    chunk.content,
                    max_chars=settings.embedding_input_max_chars,
                )
                embedding_inputs.append(embedding_input)
                truncated_chunks += int(truncated)
        timed_out = False
        try:
            vectors = embedder.embed(embedding_inputs) if should_embed else []
        except httpx.TimeoutException:
            vectors = []
            timed_out = True
        if should_embed and any(len(vector) != settings.embedding_dimension for vector in vectors):
            raise RuntimeError("embedding dimension mismatch; pipeline stopped fail-closed")
        embedding_status = (
            "deferred_runtime"
            if embedding_deferred or timed_out
            else "complete"
            if should_embed
            else "skipped_policy"
            if chunks and not policy_allowed
            else "skipped_cost_limit"
            if chunks and policy_allowed and not cost_allowed
            else "pending"
            if not embedder and chunks and embedding_allowed
            else "not_required"
        )
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
                "embedding_revision": settings.embedding_revision if should_embed else None,
                "embedding_status": embedding_status,
                "embedding_skip_reason": embedding_skip_reason,
                "embedding_defer_reason": (
                    runtime_reason
                    if embedding_deferred
                    else "embedding_request_timeout"
                    if timed_out
                    else None
                ),
                "embedding_chunk_count": len(chunks),
                "embedding_character_count": sum(len(chunk.content) for chunk in chunks),
                "embedding_policy": SEMANTIC_POLICY_VERSION,
                "embedding_input_max_chars": (
                    settings.embedding_input_max_chars if should_embed else None
                ),
                "embedding_input_truncated_chunks": truncated_chunks,
                "project_key": existing.project_key,
                "project_relative_path": identity.relative_path,
                "outgoing_link_count": link_count,
                "resolved_incoming_link_count": resolved_link_count,
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
            if should_embed:
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
        job.document_id = existing.id
        session.add(
            IngestEvent(
                source_root_id=root.id,
                document_id=existing.id,
                event_type="indexed",
                path=str(canonical),
                details={
                    "version_id": str(version.id),
                    "chunks": len(chunks),
                    "embedding_status": embedding_status,
                    "embedding_skip_reason": embedding_skip_reason,
                },
            )
        )
        finish(session, job)
        heartbeat(session, worker_id, "idle", success=True)
    except Exception as exc:
        fail(session, job, exc)
        heartbeat(session, worker_id, "error", failure=True)
        raise
