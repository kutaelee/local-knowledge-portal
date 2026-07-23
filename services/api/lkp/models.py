import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class DocumentState(str, enum.Enum):
    active = "active"
    deleted = "deleted"
    ignored = "ignored"
    unsupported = "unsupported"
    error = "error"


class JobStatus(str, enum.Enum):
    pending = "pending"
    leased = "leased"
    processing = "processing"
    succeeded = "succeeded"
    failed = "failed"
    dead_letter = "dead_letter"
    cancelled = "cancelled"


class SourceRoot(Base):
    __tablename__ = "source_root"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    canonical_path: Mapped[str] = mapped_column(Text, unique=True)
    source_type: Mapped[str] = mapped_column(String(32))
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    include_patterns: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    exclude_patterns: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("source_root_id", "canonical_path", name="uq_document_root_path"),
        Index(
            "ix_document_relative_path_trgm",
            "relative_path",
            postgresql_using="gin",
            postgresql_ops={"relative_path": "gin_trgm_ops"},
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_root_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_root.id"))
    canonical_path: Mapped[str] = mapped_column(Text)
    relative_path: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    extension: Mapped[str] = mapped_column(String(32))
    mime_type: Mapped[str | None] = mapped_column(String(200))
    project_key: Mapped[str | None] = mapped_column(String(200))
    parent_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    modified_at_fs: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current_content_hash: Mapped[str | None] = mapped_column(String(64))
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    state: Mapped[DocumentState] = mapped_column(Enum(DocumentState, name="document_state"))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DocumentVersion(Base):
    __tablename__ = "document_version"
    __table_args__ = (UniqueConstraint("document_id", "content_hash", name="uq_version_doc_hash"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"))
    content_hash: Mapped[str] = mapped_column(String(64))
    byte_size: Mapped[int] = mapped_column(BigInteger)
    created_at_fs: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    modified_at_fs: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    parser_version: Mapped[str] = mapped_column(String(100))
    chunker_version: Mapped[str] = mapped_column(String(100))
    line_count: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    change_type: Mapped[str] = mapped_column(String(32))
    diff_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class DocumentChunk(Base):
    __tablename__ = "document_chunk"
    __table_args__ = (UniqueConstraint("document_version_id", "chunk_index"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_version.id"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    chunk_type: Mapped[str] = mapped_column(String(32))
    heading_path: Mapped[str | None] = mapped_column(Text)
    symbol_name: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(String(50))
    start_line: Mapped[int] = mapped_column(Integer)
    end_line: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    token_estimate: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    lexical_search_vector: Mapped[Any] = mapped_column(
        TSVECTOR, server_default=text("''::tsvector")
    )


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embedding"
    __table_args__ = (UniqueConstraint("chunk_id", "embedding_revision"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chunk_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_chunk.id"))
    embedding_revision: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    model_digest: Mapped[str] = mapped_column(String(200))
    dimension: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IngestJob(Base):
    __tablename__ = "ingest_job"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_job_idempotency"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(128))
    source_root_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("source_root.id"))
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"))
    canonical_path: Mapped[str] = mapped_column(Text)
    job_type: Mapped[str] = mapped_column(String(50))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus, name="job_status"))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    leased_by: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_type: Mapped[str | None] = mapped_column(String(200))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeat"
    worker_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(200))
    process_id: Mapped[int] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    current_job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    state: Mapped[str] = mapped_column(String(32))
    processed_count: Mapped[int] = mapped_column(BigInteger, default=0)
    failed_count: Mapped[int] = mapped_column(BigInteger, default=0)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class IngestEvent(Base):
    __tablename__ = "ingest_event"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_root_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(50))
    path: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Tag(Base):
    __tablename__ = "tag"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)


class DocumentTag(Base):
    __tablename__ = "document_tag"
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"), primary_key=True)
    tag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tag.id"), primary_key=True)


class DocumentLink(Base):
    __tablename__ = "document_link"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document.id"))
    target_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document.id"))
    raw_target: Mapped[str] = mapped_column(Text)
    link_type: Mapped[str] = mapped_column(String(32))
    source_line: Mapped[int | None] = mapped_column(Integer)


class SearchQueryLog(Base):
    __tablename__ = "search_query_log"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query_hash: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32))
    result_count: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GeneratedPage(Base):
    __tablename__ = "generated_page"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    relative_path: Mapped[str] = mapped_column(Text, unique=True)
    source_hashes: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    pipeline_version: Mapped[str] = mapped_column(String(100))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackupRun(Base):
    __tablename__ = "backup_run"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    backup_path: Mapped[str] = mapped_column(Text, unique=True)
    status: Mapped[str] = mapped_column(String(32))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SystemSetting(Base):
    __tablename__ = "system_setting"
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
