import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from difflib import unified_diff
from functools import lru_cache

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from lkp_indexer.embedding import CachedEmbedder, OllamaEmbedder
from lkp_indexer.knowledge import create_candidate, evaluate_gate, publish_candidate
from lkp_indexer.queue import retry_as_new
from lkp_indexer.selection import CODE_EXTENSIONS
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from .db import get_db
from .logging import configure_logging
from .models import (
    ActivityEvent,
    BackupRun,
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentLink,
    DocumentState,
    DocumentVersion,
    EvidenceRecord,
    IngestEvent,
    IngestJob,
    JobStatus,
    KnowledgeCandidate,
    KnowledgeCase,
    KnowledgeCaseRelation,
    KnowledgeCaseRevision,
    KnowledgeOccurrence,
    SourceRoot,
    WorkerHeartbeat,
)
from .schemas import CandidateCreate, RagRequest, SearchRequest, SearchResponse
from .search import search
from .settings import get_settings

settings = get_settings()
configure_logging("api")
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.query_embedding_prewarm:
        started = time.perf_counter()
        try:
            await asyncio.to_thread(
                _embedder().embed,
                ["local knowledge portal query cache warmup"],
            )
            logger.info(
                "query_embedding_prewarmed",
                duration_ms=int((time.perf_counter() - started) * 1000),
                model=settings.embedding_model,
                revision=settings.embedding_revision,
            )
        except Exception as exc:
            logger.warning(
                "query_embedding_prewarm_failed",
                duration_ms=int((time.perf_counter() - started) * 1000),
                error_type=type(exc).__name__,
            )
    yield


app = FastAPI(
    title="Local Knowledge Portal API",
    version="0.1.0",
    description="Read-only provenance-first local knowledge and RAG API",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-Trace-Id"],
)


@app.middleware("http")
async def request_log(request: Request, call_next):
    started = time.perf_counter()
    trace_id = request.headers.get("x-trace-id", str(uuid.uuid4()))
    try:
        response = await call_next(request)
        response.headers["x-trace-id"] = trace_id
        logger.info(
            "http_request",
            trace_id=trace_id,
            path=request.url.path,
            duration_ms=int((time.perf_counter() - started) * 1000),
            status=response.status_code,
            job_id=None,
            worker_id=None,
            document_id=None,
            error_type=None,
        )
        return response
    except Exception as exc:
        logger.exception(
            "http_error",
            trace_id=trace_id,
            path=request.url.path,
            duration_ms=int((time.perf_counter() - started) * 1000),
            error_type=type(exc).__name__,
        )
        raise


@lru_cache(maxsize=1)
def _embedder() -> CachedEmbedder:
    return CachedEmbedder(
        OllamaEmbedder(
            settings.ollama_base_url,
            settings.embedding_model,
            settings.embedding_model_digest,
            settings.embedding_dimension,
            settings.query_embedding_timeout_seconds,
        ),
        max_entries=settings.query_embedding_cache_size,
        ttl_seconds=settings.query_embedding_cache_ttl_seconds,
    )


@app.get("/health/live")
def live() -> dict:
    return {"status": "live", "service": "api", "version": app.version}


@app.get("/health/ready")
def ready(db: Session = Depends(get_db)) -> dict:
    try:
        database_version = db.execute(text("select version()")).scalar_one()
        revision = db.execute(text("select version_num from alembic_version")).scalar_one()
        database = True
    except Exception as exc:
        raise HTTPException(503, detail={"database": False, "error": type(exc).__name__}) from exc
    try:
        response = httpx.get(f"{settings.ollama_base_url}/api/version", timeout=1)
        ollama = response.is_success
    except httpx.HTTPError:
        ollama = False
    return {
        "status": "ready",
        "database": database,
        "database_version": database_version,
        "schema_revision": revision,
        "ollama": ollama,
        "embedding_revision": settings.embedding_revision,
        "query_embedding_cache": _embedder().cache_info(),
        "generation": {
            "enabled": settings.generation_provider != "disabled",
            "provider": settings.generation_provider,
            "model": settings.generation_model or None,
        },
    }


@app.get("/api/v1/search", response_model=SearchResponse)
def keyword_search(
    q: str = Query(min_length=1, max_length=500),
    top_k: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> SearchResponse:
    response = search(db, SearchRequest(query=q, mode="keyword", top_k=top_k), settings)
    db.commit()
    return response


@app.post("/api/v1/search/hybrid", response_model=SearchResponse)
def hybrid_search(request: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    embedder = _embedder() if request.mode in {"semantic", "hybrid"} else None
    try:
        response = search(db, request, settings, embedder)
    except httpx.HTTPError:
        if request.mode == "semantic":
            return SearchResponse(
                query=request.query, mode=request.mode, confidence="none", results=[], total=0
            )
        response = search(db, request.model_copy(update={"mode": "keyword"}), settings)
        response.mode = "hybrid-degraded-keyword-only"
        response.confidence = "low" if response.results else "none"
    db.commit()
    return response


@app.post("/api/v1/rag/context")
def rag_context(request: RagRequest, db: Session = Depends(get_db)) -> dict:
    search_request = SearchRequest(
        query=request.query,
        mode="hybrid",
        top_k=request.top_k,
        project=request.filters.get("project"),
        path_prefix=request.filters.get("path_prefix"),
    )
    response = hybrid_search(search_request, db)
    used = 0
    contexts = []
    for result in response.results:
        if used + len(result.snippet) > request.max_chars:
            break
        contexts.append(
            {
                "content": result.snippet,
                "retrieval_score": result.fused_rank,
                "provenance": result.provenance.model_dump(mode="json"),
            }
        )
        used += len(result.snippet)
    return {
        "query": request.query,
        "confidence": response.confidence,
        "context": contexts,
        "no_answer": len(contexts) == 0,
    }


@app.get("/api/v1/documents")
def documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    state: DocumentState = DocumentState.active,
    db: Session = Depends(get_db),
) -> dict:
    statement = (
        select(Document)
        .join(SourceRoot, SourceRoot.id == Document.source_root_id)
        .where(Document.state == state, SourceRoot.data_scope == "production")
        .order_by(Document.modified_at_fs.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = db.scalars(statement).all()
    total = db.scalar(
        select(func.count())
        .select_from(Document)
        .join(SourceRoot, SourceRoot.id == Document.source_root_id)
        .where(Document.state == state, SourceRoot.data_scope == "production")
    )
    return {"items": [_document_json(row) for row in rows], "page": page, "total": total}


def _document_json(row: Document) -> dict:
    return {
        "id": str(row.id),
        "source_root_id": str(row.source_root_id),
        "canonical_path": row.canonical_path,
        "relative_path": row.relative_path,
        "filename": row.filename,
        "project": row.project_key,
        "state": row.state.value,
        "size_bytes": row.size_bytes,
        "modified_at": row.modified_at_fs,
        "content_hash": row.current_content_hash,
        "current_version_id": row.current_version_id,
    }


@app.get("/api/v1/documents/{document_id}")
def document_detail(document_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(Document, document_id)
    if not row:
        raise HTTPException(404, "document not found")
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_version_id == row.current_version_id)
        .order_by(DocumentChunk.chunk_index)
    ).all()
    return {
        **_document_json(row),
        "chunks": [
            {
                "id": str(chunk.id),
                "index": chunk.chunk_index,
                "type": chunk.chunk_type,
                "heading": chunk.heading_path,
                "symbol": chunk.symbol_name,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "content": chunk.content,
                "metadata": chunk.metadata_json,
            }
            for chunk in chunks
        ],
    }


@app.get("/api/v1/documents/{document_id}/versions")
def versions(document_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.detected_at.desc())
    ).all()
    return [
        {
            "id": str(row.id),
            "content_hash": row.content_hash,
            "change_type": row.change_type,
            "detected_at": row.detected_at,
            "line_count": row.line_count,
            "parser_version": row.parser_version,
            "chunker_version": row.chunker_version,
            "metadata": row.metadata_json,
            "diff_summary": row.diff_summary,
        }
        for row in rows
    ]


def _version_text(db: Session, version_id: uuid.UUID) -> str:
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_version_id == version_id)
        .order_by(DocumentChunk.chunk_index)
    ).all()
    if not chunks:
        return ""
    lines: dict[int, str] = {}
    for chunk in chunks:
        for offset, value in enumerate(chunk.content.splitlines(), start=chunk.start_line):
            lines.setdefault(offset, value)
    return "\n".join(lines[number] for number in sorted(lines))


@app.get("/api/v1/documents/{document_id}/diff")
def document_diff(
    document_id: uuid.UUID,
    from_version: uuid.UUID,
    to_version: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    rows = db.scalars(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.id.in_([from_version, to_version]),
        )
    ).all()
    if {row.id for row in rows} != {from_version, to_version}:
        raise HTTPException(404, "document version not found")
    before = _version_text(db, from_version)
    after = _version_text(db, to_version)
    diff = list(
        unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=str(from_version),
            tofile=str(to_version),
            lineterm="",
        )
    )
    return {
        "document_id": str(document_id),
        "from_version": str(from_version),
        "to_version": str(to_version),
        "lines": diff,
        "changed": before != after,
    }


@app.get("/api/v1/documents/{document_id}/backlinks")
def backlinks(document_id: uuid.UUID, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(DocumentLink).where(DocumentLink.target_document_id == document_id)
    ).all()
    return [
        {
            "source_document_id": row.source_document_id,
            "line": row.source_line,
            "type": row.link_type,
        }
        for row in rows
    ]


@app.get("/api/v1/projects")
def projects(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(Document.project_key, func.count(Document.id))
        .join(SourceRoot, SourceRoot.id == Document.source_root_id)
        .where(
            Document.state == DocumentState.active,
            SourceRoot.data_scope == "production",
        )
        .group_by(Document.project_key)
        .order_by(func.count(Document.id).desc())
    ).all()
    return [{"key": name, "document_count": count} for name, count in rows]


def _activity_json(row: ActivityEvent) -> dict:
    return {
        "id": str(row.id),
        "event_key": row.event_key,
        "session_id": row.session_id,
        "turn_id": row.turn_id,
        "event_type": row.event_type,
        "occurred_at": row.occurred_at,
        "project": row.project_key,
        "cwd": row.cwd,
        "instruction": row.instruction,
        "tool_name": row.tool_name,
        "command": row.command,
        "exit_code": row.exit_code,
        "changed_files": row.changed_files,
        "document_version_ids": [str(value) for value in row.document_version_ids],
        "reported_result": row.reported_result,
        "verified_result": row.verified_result,
        "verification_status": row.verification_status,
        "metadata": row.metadata_json,
    }


@app.get("/api/v1/activities")
def activities(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    event_type: str | None = None,
    verification_status: str | None = None,
    db: Session = Depends(get_db),
) -> dict:
    statement = select(ActivityEvent)
    count_statement = select(func.count()).select_from(ActivityEvent)
    if event_type:
        statement = statement.where(ActivityEvent.event_type == event_type)
        count_statement = count_statement.where(ActivityEvent.event_type == event_type)
    if verification_status:
        statement = statement.where(
            ActivityEvent.verification_status == verification_status
        )
        count_statement = count_statement.where(
            ActivityEvent.verification_status == verification_status
        )
    rows = db.scalars(
        statement.order_by(ActivityEvent.occurred_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_activity_json(row) for row in rows],
        "page": page,
        "total": db.scalar(count_statement),
    }


@app.get("/api/v1/activities/{activity_id}")
def activity_detail(activity_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(ActivityEvent, activity_id)
    if not row:
        raise HTTPException(404, "activity not found")
    evidence = db.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.activity_id == activity_id)
        .order_by(EvidenceRecord.created_at)
    ).all()
    return {
        **_activity_json(row),
        "evidence": [_evidence_json(item) for item in evidence],
    }


def _evidence_json(row: EvidenceRecord) -> dict:
    return {
        "id": str(row.id),
        "activity_id": str(row.activity_id) if row.activity_id else None,
        "candidate_id": str(row.candidate_id) if row.candidate_id else None,
        "type": row.evidence_type,
        "claim": row.claim,
        "locator": row.locator,
        "reported_value": row.reported_value,
        "verified_value": row.verified_value,
        "exit_code": row.exit_code,
        "verified": row.verified,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
    }


def _candidate_json(row: KnowledgeCandidate) -> dict:
    return {
        "id": str(row.id),
        "category": row.category,
        "title": row.title,
        "problem": row.problem,
        "symptom": row.symptom,
        "root_cause": row.root_cause,
        "solution": row.solution,
        "status": row.status,
        "evidence_gate_status": row.evidence_gate_status,
        "reported_result": row.reported_result,
        "verified_result": row.verified_result,
        "metadata": row.metadata_json,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@app.get("/api/v1/knowledge/candidates")
def candidates(
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    statement = select(KnowledgeCandidate)
    count_statement = select(func.count()).select_from(KnowledgeCandidate)
    if status:
        statement = statement.where(KnowledgeCandidate.status == status)
        count_statement = count_statement.where(KnowledgeCandidate.status == status)
    rows = db.scalars(
        statement.order_by(KnowledgeCandidate.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_candidate_json(row) for row in rows],
        "page": page,
        "total": db.scalar(count_statement),
    }


@app.post("/api/v1/knowledge/candidates")
def add_candidate(request: CandidateCreate, db: Session = Depends(get_db)) -> dict:
    row = create_candidate(
        db,
        **request.model_dump(mode="python", exclude={"evidence"}),
        evidence=[item.model_dump(mode="python") for item in request.evidence],
    )
    gate = evaluate_gate(db, row)
    db.commit()
    return {**_candidate_json(row), "evidence_gate_status": gate}


def _candidate_or_404(db: Session, candidate_id: uuid.UUID) -> KnowledgeCandidate:
    row = db.get(KnowledgeCandidate, candidate_id)
    if not row:
        raise HTTPException(404, "candidate not found")
    return row


@app.get("/api/v1/knowledge/candidates/{candidate_id}")
def candidate_detail(candidate_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    row = _candidate_or_404(db, candidate_id)
    evidence = db.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.candidate_id == row.id)
        .order_by(EvidenceRecord.created_at)
    ).all()
    return {**_candidate_json(row), "evidence": [_evidence_json(item) for item in evidence]}


@app.post("/api/v1/knowledge/candidates/{candidate_id}/evaluate")
def evaluate_candidate(candidate_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    row = _candidate_or_404(db, candidate_id)
    result = evaluate_gate(db, row)
    db.commit()
    return {"id": str(row.id), "evidence_gate_status": result, "status": row.status}


@app.post("/api/v1/knowledge/candidates/{candidate_id}/publish")
def publish(candidate_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    row = _candidate_or_404(db, candidate_id)
    case, outcome = publish_candidate(db, row)
    db.commit()
    return {
        "candidate_id": str(row.id),
        "case_id": str(case.id) if case else None,
        "outcome": outcome,
        "status": row.status,
    }


def _case_json(row: KnowledgeCase) -> dict:
    return {
        "id": str(row.id),
        "category": row.category,
        "title": row.title,
        "problem": row.problem,
        "symptom": row.symptom,
        "root_cause": row.root_cause,
        "solution": row.solution,
        "status": row.status,
        "occurrence_count": row.occurrence_count,
        "current_revision_id": str(row.current_revision_id)
        if row.current_revision_id
        else None,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "metadata": row.metadata_json,
    }


@app.get("/api/v1/knowledge/cases")
def knowledge_cases(
    category: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    statement = select(KnowledgeCase).where(KnowledgeCase.status == "verified")
    count_statement = (
        select(func.count())
        .select_from(KnowledgeCase)
        .where(KnowledgeCase.status == "verified")
    )
    if category:
        statement = statement.where(KnowledgeCase.category == category)
        count_statement = count_statement.where(KnowledgeCase.category == category)
    rows = db.scalars(
        statement.order_by(KnowledgeCase.last_seen_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_case_json(row) for row in rows],
        "page": page,
        "total": db.scalar(count_statement),
    }


@app.get("/api/v1/knowledge/cases/{case_id}")
def knowledge_case_detail(case_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(KnowledgeCase, case_id)
    if not row or row.status != "verified":
        raise HTTPException(404, "verified knowledge case not found")
    revisions = db.scalars(
        select(KnowledgeCaseRevision)
        .where(KnowledgeCaseRevision.case_id == case_id)
        .order_by(KnowledgeCaseRevision.revision_number.desc())
    ).all()
    occurrences = db.scalars(
        select(KnowledgeOccurrence)
        .where(KnowledgeOccurrence.case_id == case_id)
        .order_by(KnowledgeOccurrence.occurred_at.desc())
    ).all()
    relations = db.scalars(
        select(KnowledgeCaseRelation).where(
            (KnowledgeCaseRelation.source_case_id == case_id)
            | (KnowledgeCaseRelation.target_case_id == case_id)
        )
    ).all()
    return {
        **_case_json(row),
        "revisions": [
            {
                "id": str(item.id),
                "number": item.revision_number,
                "content": item.content_json,
                "evidence_summary": item.evidence_summary,
                "created_at": item.created_at,
            }
            for item in revisions
        ],
        "occurrences": [
            {
                "id": str(item.id),
                "candidate_id": str(item.candidate_id),
                "activity_id": str(item.activity_id) if item.activity_id else None,
                "evidence": item.evidence_json,
                "occurred_at": item.occurred_at,
            }
            for item in occurrences
        ],
        "relations": [
            {
                "source_case_id": str(item.source_case_id),
                "target_case_id": str(item.target_case_id),
                "type": item.relation_type,
            }
            for item in relations
        ],
    }


@app.get("/api/v1/tree")
def tree(
    project: str | None = None, limit: int = Query(5000, le=20000), db: Session = Depends(get_db)
) -> dict:
    statement = (
        select(Document)
        .join(SourceRoot, SourceRoot.id == Document.source_root_id)
        .where(
            Document.state == DocumentState.active,
            SourceRoot.data_scope == "production",
        )
    )
    if project:
        statement = statement.where(Document.project_key == project)
    rows = db.scalars(
        statement.order_by(Document.project_key, Document.project_relative_path).limit(limit)
    ).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "source_root_id": str(row.source_root_id),
                "project": row.project_key,
                "path": row.project_relative_path,
                "source_relative_path": row.relative_path,
                "state": row.state.value,
            }
            for row in rows
        ],
        "truncated": len(rows) == limit,
    }


@app.get("/api/v1/jobs")
def jobs(
    status: JobStatus | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    statement = select(IngestJob)
    if status:
        statement = statement.where(IngestJob.status == status)
    rows = db.scalars(
        statement.order_by(IngestJob.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "status": row.status.value,
                "job_type": row.job_type,
                "path": row.canonical_path,
                "attempt_count": row.attempt_count,
                "max_attempts": row.max_attempts,
                "leased_by": row.leased_by,
                "error_type": row.error_type,
                "error_message": row.error_message,
                "created_at": row.created_at,
                "finished_at": row.finished_at,
            }
            for row in rows
        ],
        "page": page,
    }


@app.post("/api/v1/jobs/{job_id}/retry")
def retry_job(job_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    try:
        new_job = retry_as_new(db, job_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.add(
        IngestEvent(
            event_type="job_retry",
            details={
                "original_job_id": str(job_id),
                "new_job_id": str(new_job.id),
            },
        )
    )
    db.commit()
    return {"id": str(new_job.id), "status": new_job.status.value, "retry_of": str(job_id)}


@app.get("/api/v1/workers")
def workers(db: Session = Depends(get_db)) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc())).all()
    return [
        {
            "worker_id": row.worker_id,
            "hostname": row.hostname,
            "state": "stale"
            if now - row.last_seen_at > timedelta(seconds=settings.stale_after_seconds)
            else row.state,
            "last_seen_at": row.last_seen_at,
            "current_job_id": row.current_job_id,
            "processed_count": row.processed_count,
            "failed_count": row.failed_count,
            "metadata": row.metadata_json or {},
        }
        for row in rows
    ]


@app.get("/api/v1/backups")
def backups(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(BackupRun).order_by(BackupRun.created_at.desc()).limit(100)
    ).all()
    return [
        {
            "id": str(row.id),
            "path": row.backup_path,
            "status": row.status,
            "manifest": row.manifest,
            "created_at": row.created_at,
            "finished_at": row.finished_at,
        }
        for row in rows
    ]


@app.get("/api/v1/timeline")
def timeline(limit: int = Query(100, le=500), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(IngestEvent).order_by(IngestEvent.created_at.desc()).limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "event": row.event_type,
            "path": row.path,
            "details": row.details,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@app.get("/api/v1/metrics/summary")
def metrics_summary(db: Session = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc)
    queue_window_hours = 3
    counts = dict(
        db.execute(select(IngestJob.status, func.count()).group_by(IngestJob.status)).all()
    )
    oldest = db.scalar(
        select(func.extract("epoch", func.now() - func.min(IngestJob.created_at))).where(
            IngestJob.status == JobStatus.pending
        )
    )
    succeeded_in_window = db.scalar(
        select(func.count())
        .select_from(IngestJob)
        .where(
            IngestJob.status == JobStatus.succeeded,
            IngestJob.finished_at >= now - timedelta(hours=queue_window_hours),
        )
    ) or 0
    failed_in_window = db.scalar(
        select(func.count())
        .select_from(IngestJob)
        .where(
            IngestJob.status.in_([JobStatus.failed, JobStatus.dead_letter]),
            IngestJob.finished_at >= now - timedelta(hours=1),
        )
    ) or 0
    queue_rate_per_hour = float(succeeded_in_window) / queue_window_hours
    pending_count = int(counts.get(JobStatus.pending, 0))
    queue_eta_seconds = (
        pending_count / queue_rate_per_hour * 3600
        if pending_count and queue_rate_per_hour > 0
        else None
    )
    latest_indexed_at = db.scalar(
        select(func.max(DocumentVersion.detected_at))
        .join(Document, Document.current_version_id == DocumentVersion.id)
        .join(SourceRoot, SourceRoot.id == Document.source_root_id)
        .where(
            Document.state == DocumentState.active,
            SourceRoot.data_scope == "production",
        )
    )
    latest_source_modified_at = db.scalar(
        select(func.max(Document.modified_at_fs))
        .join(SourceRoot, SourceRoot.id == Document.source_root_id)
        .where(
            Document.state == DocumentState.active,
            SourceRoot.data_scope == "production",
        )
    )
    throughput = [
        {"bucket": bucket, "count": count}
        for bucket, count in db.execute(
            text(
                """
                SELECT date_trunc('hour', created_at) AS bucket, count(*) AS count
                FROM ingest_event
                WHERE event_type = 'indexed'
                  AND created_at >= now() - interval '12 hours'
                GROUP BY bucket
                ORDER BY bucket
                """
            )
        ).all()
    ]
    recent_documents = [
        {
            "id": str(document_id),
            "filename": filename,
            "relative_path": relative_path,
            "project": project_key,
            "source_root": source_root_name,
            "modified_at": modified_at,
            "indexed_at": indexed_at,
            "change_type": change_type,
        }
        for (
            document_id,
            filename,
            relative_path,
            project_key,
            source_root_name,
            modified_at,
            indexed_at,
            change_type,
        ) in db.execute(
            select(
                Document.id,
                Document.filename,
                Document.relative_path,
                Document.project_key,
                SourceRoot.name,
                Document.modified_at_fs,
                DocumentVersion.detected_at,
                DocumentVersion.change_type,
            )
            .join(SourceRoot, SourceRoot.id == Document.source_root_id)
            .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
            .where(
                Document.state == DocumentState.active,
                SourceRoot.data_scope == "production",
            )
            .order_by(DocumentVersion.detected_at.desc())
            .limit(8)
        ).all()
    ]
    source_roots = [
        {
            "id": str(root_id),
            "name": name,
            "source_type": source_type,
            "document_count": document_count,
            "last_reconciled_at": last_reconciled_at,
            "last_seen_at": last_seen_at,
        }
        for (
            root_id,
            name,
            source_type,
            last_reconciled_at,
            document_count,
            last_seen_at,
        ) in db.execute(
            select(
                SourceRoot.id,
                SourceRoot.name,
                SourceRoot.source_type,
                SourceRoot.last_reconciled_at,
                func.count(Document.id).filter(Document.state == DocumentState.active),
                func.max(Document.last_seen_at),
            )
            .outerjoin(Document, Document.source_root_id == SourceRoot.id)
            .where(
                SourceRoot.enabled.is_(True),
                SourceRoot.data_scope == "production",
            )
            .group_by(SourceRoot.id)
            .order_by(SourceRoot.name)
        ).all()
    ]
    current_workers = [
        item for item in workers(db) if not item["metadata"].get("retired", False)
    ]
    worker_states: dict[str, int] = {}
    for item in current_workers:
        worker_states[item["state"]] = worker_states.get(item["state"], 0) + 1
    active_worker_states = {"healthy", "busy", "idle", "cooldown", "paused"}
    document_breakdown_row = db.execute(
        select(
            func.count(Document.id)
            .filter(
                or_(
                    SourceRoot.source_type == "obsidian",
                    Document.extension.in_([".md", ".mdx"]),
                )
            )
            .label("knowledge_documents"),
            func.count(Document.id)
            .filter(Document.extension.in_(sorted(CODE_EXTENSIONS)))
            .label("code_files"),
            func.count(Document.id)
            .filter(
                ~Document.extension.in_(
                    sorted(CODE_EXTENSIONS | {".md", ".mdx"})
                ),
                SourceRoot.source_type != "obsidian",
            )
            .label("support_files"),
        )
        .select_from(Document)
        .join(SourceRoot, SourceRoot.id == Document.source_root_id)
        .where(
            Document.state == DocumentState.active,
            SourceRoot.data_scope == "production",
        )
    ).one()
    current_chunks = int(
        db.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .join(Document, Document.current_version_id == DocumentChunk.document_version_id)
            .join(SourceRoot, SourceRoot.id == Document.source_root_id)
            .where(
                Document.state == DocumentState.active,
                SourceRoot.data_scope == "production",
            )
        )
        or 0
    )
    semantic_chunks = int(
        db.scalar(
            select(func.count())
            .select_from(ChunkEmbedding)
            .join(DocumentChunk, DocumentChunk.id == ChunkEmbedding.chunk_id)
            .join(Document, Document.current_version_id == DocumentChunk.document_version_id)
            .join(SourceRoot, SourceRoot.id == Document.source_root_id)
            .where(
                Document.state == DocumentState.active,
                SourceRoot.data_scope == "production",
                ChunkEmbedding.embedding_revision == settings.embedding_revision,
            )
        )
        or 0
    )
    pending_initial = int(
        db.scalar(
            select(func.count())
            .select_from(IngestJob)
            .where(IngestJob.status == JobStatus.pending, IngestJob.job_type == "index")
        )
        or 0
    )
    pending_live = max(0, int(counts.get(JobStatus.pending, 0)) - pending_initial)
    search_latency = {
        row["mode"]: {
            "queries": int(row["queries"]),
            "p50_ms": float(row["p50_ms"]),
            "p95_ms": float(row["p95_ms"]),
        }
        for row in db.execute(
            text(
                """
                SELECT mode, count(*) AS queries,
                  percentile_cont(0.50) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
                  percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms
                FROM search_query_log
                WHERE created_at >= now() - interval '1 hour'
                GROUP BY mode
                ORDER BY mode
                """
            )
        ).mappings()
    }
    return {
        "generated_at": now,
        "projects": db.scalar(
            select(func.count(func.distinct(Document.project_key)))
            .join(SourceRoot, SourceRoot.id == Document.source_root_id)
            .where(
                Document.state == DocumentState.active,
                SourceRoot.data_scope == "production",
            )
        ),
        "documents": db.scalar(
            select(func.count())
            .select_from(Document)
            .join(SourceRoot, SourceRoot.id == Document.source_root_id)
            .where(
                Document.state == DocumentState.active,
                SourceRoot.data_scope == "production",
            )
        ),
        "chunks": current_chunks,
        "semantic_chunks": semantic_chunks,
        "semantic_coverage": (
            semantic_chunks / current_chunks if current_chunks else 0.0
        ),
        "document_breakdown": {
            "knowledge_documents": int(document_breakdown_row.knowledge_documents),
            "code_files": int(document_breakdown_row.code_files),
            "support_files": int(document_breakdown_row.support_files),
        },
        "pending_breakdown": {
            "initial_scan": pending_initial,
            "live_changes": pending_live,
        },
        "jobs": {key.value: value for key, value in counts.items()},
        "oldest_pending_seconds": float(oldest or 0),
        "queue_rate_per_hour": queue_rate_per_hour,
        "queue_eta_seconds": queue_eta_seconds,
        "succeeded_last_3h": int(succeeded_in_window),
        "failed_last_hour": int(failed_in_window),
        "workers": sum(
            count for state, count in worker_states.items() if state in active_worker_states
        ),
        "worker_states": worker_states,
        "latest_indexed_at": latest_indexed_at,
        "latest_source_modified_at": latest_source_modified_at,
        "throughput": throughput,
        "recent_documents": recent_documents,
        "source_roots": source_roots,
        "embedding_model": settings.embedding_model,
        "embedding_revision": settings.embedding_revision,
        "pipeline_version": settings.pipeline_version,
        "repository_embedding_mode": settings.repository_embedding_mode,
        "query_embedding_cache": _embedder().cache_info(),
        "search_latency_last_hour": search_latency,
    }


@app.get("/metrics", response_class=PlainTextResponse)
def prometheus_metrics(db: Session = Depends(get_db)) -> str:
    summary = metrics_summary(db)
    lines = [
        "# TYPE lkp_documents gauge",
        f"lkp_documents {summary['documents']}",
        "# TYPE lkp_chunks gauge",
        f"lkp_chunks {summary['chunks']}",
        "# TYPE lkp_oldest_pending_seconds gauge",
        f"lkp_oldest_pending_seconds {summary['oldest_pending_seconds']}",
    ]
    for status, count in summary["jobs"].items():
        lines.append(f'lkp_jobs{{status="{status}"}} {count}')
    return "\n".join(lines) + "\n"
