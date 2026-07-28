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
    data_scope: Mapped[str] = mapped_column(String(32), default="production")
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    include_patterns: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    exclude_patterns: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    # Keep documents discoverable while allowing source-root-specific semantic
    # exclusions for generated evidence or bulk artifacts. This must never be
    # reused as a filesystem ignore list: lexical search and provenance remain
    # available for these files.
    semantic_exclude_patterns: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
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
    project_relative_path: Mapped[str] = mapped_column(Text, default="")
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


class KnowledgeSimilarityEmbedding(Base):
    """Rebuildable vectors used only to compare candidates with canonical cases.

    They are intentionally separate from document retrieval embeddings so a
    candidate cannot become searchable/published merely by receiving a vector.
    """

    __tablename__ = "knowledge_similarity_embedding"
    __table_args__ = (
        UniqueConstraint(
            "record_type",
            "record_id",
            "embedding_revision",
            name="uq_knowledge_similarity_embedding_record_revision",
        ),
        Index(
            "ix_knowledge_similarity_embedding_lookup",
            "record_type",
            "embedding_revision",
        ),
        Index(
            "ix_knowledge_similarity_embedding_terms",
            "key_terms",
            postgresql_using="gin",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    record_type: Mapped[str] = mapped_column(String(32))
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    content_hash: Mapped[str] = mapped_column(String(64))
    key_terms: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    embedding_revision: Mapped[str] = mapped_column(String(200))
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    model_digest: Mapped[str] = mapped_column(String(200))
    dimension: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


class HookSpoolEvent(Base):
    __tablename__ = "hook_spool_event"
    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(100), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(100), index=True)
    event_name: Mapped[str] = mapped_column(String(50), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cwd: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    spool_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="processed")
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ActivityEvent(Base):
    __tablename__ = "activity_event"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_activity_event_key"),
        Index("ix_activity_session_occurred", "session_id", "occurred_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_key: Mapped[str] = mapped_column(String(128))
    session_id: Mapped[str] = mapped_column(String(100))
    turn_id: Mapped[str | None] = mapped_column(String(100))
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    project_key: Mapped[str | None] = mapped_column(String(200), index=True)
    cwd: Mapped[str | None] = mapped_column(Text)
    instruction: Mapped[str | None] = mapped_column(Text)
    tool_name: Mapped[str | None] = mapped_column(String(200))
    command: Mapped[str | None] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    changed_files: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    document_version_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list
    )
    reported_result: Mapped[str | None] = mapped_column(Text)
    verified_result: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(32), default="UNVERIFIED")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectJournalEntry(Base):
    """Verified project change log, independent from reusable knowledge promotion."""

    __tablename__ = "project_journal_entry"
    __table_args__ = (
        UniqueConstraint(
            "source_stop_activity_id",
            name="uq_project_journal_source_stop",
        ),
        Index(
            "ix_project_journal_project_occurred",
            "project_key",
            "occurred_at",
            "id",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_stop_activity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("activity_event.id")
    )
    project_key: Mapped[str] = mapped_column(String(200))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(Text)
    intent: Mapped[str] = mapped_column(Text)
    change_summary: Mapped[str] = mapped_column(Text)
    failures_json: Mapped[list[dict[str, Any]]] = mapped_column(
        "failures", JSONB, default=list
    )
    resolution: Mapped[str] = mapped_column(Text)
    verification_json: Mapped[list[dict[str, Any]]] = mapped_column(
        "verification", JSONB, default=list
    )
    changed_files: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    knowledge_references_json: Mapped[list[dict[str, Any]]] = mapped_column(
        "knowledge_references", JSONB, default=list
    )
    significance_reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    verification_status: Mapped[str] = mapped_column(String(32), default="UNVERIFIED")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectArticle(Base):
    """One canonical, incrementally refreshed article per project."""

    __tablename__ = "project_article"
    __table_args__ = (
        UniqueConstraint("project_key", name="uq_project_article_project"),
        Index("ix_project_article_updated", "updated_at", "project_key"),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_key: Mapped[str] = mapped_column(String(200))
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    source_hash: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="pending")
    last_compared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProjectArticleRevision(Base):
    """Append-only article revision with sentence-level provenance."""

    __tablename__ = "project_article_revision"
    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "revision_number",
            name="uq_project_article_revision_number",
        ),
        Index(
            "ix_project_article_revision_article_created",
            "article_id",
            "created_at",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("project_article.id"))
    revision_number: Mapped[int] = mapped_column(Integer)
    previous_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project_article_revision.id")
    )
    title: Mapped[str] = mapped_column(Text)
    standfirst_json: Mapped[dict[str, Any]] = mapped_column("standfirst", JSONB)
    sections_json: Mapped[list[dict[str, Any]]] = mapped_column("sections", JSONB)
    sources_json: Mapped[list[dict[str, Any]]] = mapped_column("sources", JSONB)
    source_manifest_json: Mapped[list[dict[str, Any]]] = mapped_column(
        "source_manifest", JSONB, default=list
    )
    source_hash: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(100))
    model: Mapped[str] = mapped_column(String(200))
    model_digest: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(200))
    change_summary_json: Mapped[dict[str, Any]] = mapped_column(
        "change_summary", JSONB, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeCandidate(Base):
    __tablename__ = "knowledge_candidate"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category: Mapped[str] = mapped_column(String(50), index=True)
    title: Mapped[str] = mapped_column(Text)
    problem: Mapped[str] = mapped_column(Text)
    symptom: Mapped[str] = mapped_column(Text)
    root_cause: Mapped[str] = mapped_column(Text)
    solution: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="candidate", index=True)
    evidence_gate_status: Mapped[str] = mapped_column(
        String(32), default="NEEDS_EVIDENCE", index=True
    )
    dedup_key: Mapped[str] = mapped_column(String(64), index=True)
    similarity_key: Mapped[str] = mapped_column(String(64), index=True)
    reported_result: Mapped[str | None] = mapped_column(Text)
    verified_result: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvidenceRecord(Base):
    __tablename__ = "evidence_record"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    activity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("activity_event.id"))
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("knowledge_candidate.id"))
    evidence_type: Mapped[str] = mapped_column(String(50), index=True)
    claim: Mapped[str] = mapped_column(Text)
    locator: Mapped[str | None] = mapped_column(Text)
    reported_value: Mapped[str | None] = mapped_column(Text)
    verified_value: Mapped[str | None] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeCase(Base):
    __tablename__ = "knowledge_case"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    category: Mapped[str] = mapped_column(String(50), index=True)
    title: Mapped[str] = mapped_column(Text)
    problem: Mapped[str] = mapped_column(Text)
    symptom: Mapped[str] = mapped_column(Text, index=True)
    root_cause: Mapped[str] = mapped_column(Text)
    solution: Mapped[str] = mapped_column(Text)
    dedup_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(32), default="verified", index=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    current_revision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict)


class KnowledgeCaseRevision(Base):
    __tablename__ = "knowledge_case_revision"
    __table_args__ = (UniqueConstraint("case_id", "revision_number"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_case.id"))
    revision_number: Mapped[int] = mapped_column(Integer)
    content_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    evidence_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeOccurrence(Base):
    __tablename__ = "knowledge_occurrence"
    __table_args__ = (UniqueConstraint("case_id", "candidate_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_case.id"))
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_candidate.id"))
    activity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("activity_event.id"))
    evidence_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeCaseRelation(Base):
    __tablename__ = "knowledge_case_relation"
    __table_args__ = (UniqueConstraint("source_case_id", "target_case_id", "relation_type"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_case.id"))
    target_case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_case.id"))
    relation_type: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
