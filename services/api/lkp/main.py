import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from lkp_indexer.embedding import OllamaEmbedder
from lkp_indexer.queue import retry_as_new
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .db import get_db
from .logging import configure_logging
from .models import (
    Document,
    DocumentChunk,
    DocumentLink,
    DocumentState,
    DocumentVersion,
    IngestEvent,
    IngestJob,
    JobStatus,
    WorkerHeartbeat,
)
from .schemas import RagRequest, SearchRequest, SearchResponse
from .search import search
from .settings import get_settings

settings = get_settings()
configure_logging("api")
logger = structlog.get_logger()
app = FastAPI(
    title="Local Knowledge Portal API",
    version="0.1.0",
    description="Read-only provenance-first local knowledge and RAG API",
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


def _embedder() -> OllamaEmbedder:
    return OllamaEmbedder(
        settings.ollama_base_url,
        settings.embedding_model,
        settings.embedding_model_digest,
        settings.embedding_dimension,
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
        .where(Document.state == state)
        .order_by(Document.modified_at_fs.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = db.scalars(statement).all()
    total = db.scalar(select(func.count()).select_from(Document).where(Document.state == state))
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
        .where(Document.state == DocumentState.active)
        .group_by(Document.project_key)
        .order_by(func.count(Document.id).desc())
    ).all()
    return [{"key": name, "document_count": count} for name, count in rows]


@app.get("/api/v1/tree")
def tree(
    project: str | None = None, limit: int = Query(2000, le=5000), db: Session = Depends(get_db)
) -> dict:
    statement = select(Document).where(Document.state == DocumentState.active)
    if project:
        statement = statement.where(Document.project_key == project)
    rows = db.scalars(statement.order_by(Document.relative_path).limit(limit)).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "source_root_id": str(row.source_root_id),
                "project": row.project_key,
                "path": row.relative_path,
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
    counts = dict(
        db.execute(select(IngestJob.status, func.count()).group_by(IngestJob.status)).all()
    )
    oldest = db.scalar(
        select(func.extract("epoch", func.now() - func.min(IngestJob.created_at))).where(
            IngestJob.status == JobStatus.pending
        )
    )
    return {
        "projects": db.scalar(select(func.count(func.distinct(Document.project_key)))),
        "documents": db.scalar(
            select(func.count()).select_from(Document).where(Document.state == DocumentState.active)
        ),
        "chunks": db.scalar(select(func.count()).select_from(DocumentChunk)),
        "jobs": {key.value: value for key, value in counts.items()},
        "oldest_pending_seconds": float(oldest or 0),
        "workers": len(workers(db)),
        "embedding_model": settings.embedding_model,
        "embedding_revision": settings.embedding_revision,
        "pipeline_version": settings.pipeline_version,
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
