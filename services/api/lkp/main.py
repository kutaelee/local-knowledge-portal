import asyncio
import hashlib
import json
import re
import secrets
import threading
import time
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta, timezone
from difflib import unified_diff
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from lkp_indexer.case_pages import materialize_case
from lkp_indexer.embedding import CachedEmbedder, OllamaEmbedder
from lkp_indexer.embedding_runtime import timeout_circuit_state
from lkp_indexer.knowledge import create_candidate, evaluate_gate, publish_candidate
from lkp_indexer.knowledge_curator import CURATION_HARNESS_VERSION
from lkp_indexer.queue import retry_as_new
from lkp_indexer.selection import CODE_EXTENSIONS
from lkp_indexer.service_runtime import assert_mount_guards
from sqlalchemy import and_, case, func, not_, or_, select, text
from sqlalchemy.orm import Session, aliased

from .db import get_db
from .local_chat_capture import spool_local_chat
from .logging import configure_logging
from .models import (
    ActivityEvent,
    BackupRun,
    ChunkEmbedding,
    DeveloperFeedPost,
    Document,
    DocumentChunk,
    DocumentLink,
    DocumentState,
    DocumentTag,
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
    KnowledgeSimilarityEmbedding,
    ProjectArticle,
    ProjectArticleRevision,
    ProjectJournalEntry,
    SourceRoot,
    SystemSetting,
    Tag,
    WorkerHeartbeat,
)
from .project_articles import project_article_plan, project_article_summary
from .rag_quality import build_expanded_context
from .redaction import redact_text, redact_value
from .repository_analysis_routes import router as repository_analysis_router
from .schemas import (
    CandidateCreate,
    CandidatePublish,
    GpuQueueReorderRequest,
    LocalChatCapture,
    RagRequest,
    SearchRequest,
    SearchResponse,
    ServiceControlRequest,
)
from .search import search
from .service_catalog import load_docker_groups, load_gpu_embedding_reaper
from .settings import get_settings

settings = get_settings()
_CURATION_WORKLOAD = "local-knowledge-portal-curation"
configure_logging("api")
logger = structlog.get_logger()
_SERVICE_CONFIRMATION_TTL_SECONDS = 90
_SERVICE_CONFIRMATIONS: dict[str, tuple[str, str, float]] = {}
_SERVICE_CONFIRMATION_LOCK = threading.Lock()

_KNOWLEDGE_TAG_ALIASES = {
    "사례:오류 해결": "case:error_resolution",
    "사례:구현 방식": "case:implementation",
    "사례:검증된 성공 사례": "case:custom_success",
    "사례:성능·부하": "case:performance",
    "사례:운영·장애": "case:operations",
    "작업특성:오류 해결": "situation:error_resolution",
    "작업특성:구현 방식": "situation:implementation",
    "작업특성:검증된 성공 사례": "situation:custom_success",
    "작업특성:성능·부하": "situation:performance",
    "작업특성:운영·장애": "situation:operations",
    "상태:검증됨": "lifecycle:verified",
}


def _normalize_knowledge_tag(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("프로젝트:"):
        return f"project:{cleaned.removeprefix('프로젝트:').strip().casefold()}"
    return _KNOWLEDGE_TAG_ALIASES.get(cleaned, cleaned.casefold())


def _project_article_display_status(
    article: ProjectArticle | None,
    due_reason: str | None,
) -> str:
    if article is None:
        return "pending_editor"
    if due_reason in {
        "source_changed",
        "editor_revision_changed",
        "never_compared",
        "stale",
    }:
        return "stale"
    return article.status


def _catalog_predicate(catalog: str):
    managed = and_(
        SourceRoot.source_type == "obsidian",
        Document.relative_path.like("_generated/%"),
    )
    if catalog == "source":
        return not_(managed)
    if catalog == "managed":
        return managed
    return True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    prewarm_task: asyncio.Task | None = None
    if settings.query_embedding_prewarm:
        async def prewarm() -> None:
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

        prewarm_task = asyncio.create_task(prewarm())
    yield
    if prewarm_task is not None and not prewarm_task.done():
        prewarm_task.cancel()
        with suppress(asyncio.CancelledError):
            await prewarm_task


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
app.include_router(repository_analysis_router)


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
            settings.embedding_keep_alive,
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
        assert_mount_guards(settings)
    except RuntimeError as exc:
        raise HTTPException(
            503,
            detail={"mounts": False, "error": str(exc)},
        ) from exc
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


def _http_service_status(url: str) -> tuple[str, str | None]:
    try:
        response = httpx.get(url, timeout=1.5, follow_redirects=False)
        return ("healthy", None) if response.is_success else (
            "error",
            f"HTTP {response.status_code}",
        )
    except httpx.TimeoutException:
        return "stale", "timeout"
    except httpx.HTTPError as exc:
        return "offline", type(exc).__name__


def _heartbeat_service(
    db: Session,
    *,
    key: str,
    label: str,
    prefixes: tuple[str, ...],
    now: datetime,
) -> dict:
    rows = list(
        db.scalars(
            select(WorkerHeartbeat)
            .where(or_(*(WorkerHeartbeat.worker_id.startswith(prefix) for prefix in prefixes)))
            .order_by(WorkerHeartbeat.last_seen_at.desc())
        )
    )
    if not rows:
        return {"key": key, "label": label, "state": "offline", "detail": "no heartbeat"}
    latest = rows[0]
    stale = now - latest.last_seen_at > timedelta(seconds=settings.stale_after_seconds)
    state = "stale" if stale else latest.state
    if state in {"idle", "busy", "processing"}:
        state = "healthy"
    return {
        "key": key,
        "label": label,
        "state": state,
        "detail": f"{len(rows)} heartbeat(s)",
        "last_seen_at": latest.last_seen_at,
    }


@app.get("/api/v1/system/services")
def system_services(db: Session = Depends(get_db)) -> dict:
    now = datetime.now(timezone.utc)
    revision = db.execute(text("select version_num from alembic_version")).scalar_one()
    web_state, web_error = _http_service_status("http://web:3010")
    embedding_state, embedding_error = _http_service_status(
        f"{settings.ollama_base_url}/api/version"
    )
    gpu_state, gpu_error = _http_service_status(
        f"{settings.gpu_scheduler_base_url}/api/health"
    )
    service_manager_state, service_manager_error = _http_service_status(
        f"{settings.service_manager_base_url}/api/health"
    )
    comfyui_state, comfyui_error = _http_service_status(
        "http://host.docker.internal:8188/system_stats"
    )
    ai_toolkit_state, ai_toolkit_error = _http_service_status(
        "http://host.docker.internal:8675"
    )
    if settings.generation_provider == "disabled":
        generation_state, generation_error = "disabled", None
    else:
        generation_state, generation_error = _http_service_status(
            f"{settings.generation_base_url}/api/version"
        )
        if generation_state != "healthy" and gpu_state == "healthy":
            runtime_job = _active_gpu_workload(_CURATION_WORKLOAD)
            if runtime_job and runtime_job.get("bucket") == "active":
                generation_state, generation_error = "busy", "GPU 예약에서 시작·편집 중"
            elif runtime_job:
                generation_state, generation_error = "idle", "GPU 예약 대기"
            else:
                # This service is intentionally off between scheduled batches.
                # Reporting ConnectError as an incident made a healthy idle
                # lifecycle look disconnected in the operator dashboard.
                generation_state, generation_error = "idle", "GPU 예약 시 자동 시작"
    portal_services = [
        {
            "key": "web",
            "label": "Knowledge portal UI",
            "state": web_state,
            "detail": web_error or "HTTP 200",
        },
        {
            "key": "api",
            "label": "FastAPI",
            "state": "healthy",
            "detail": app.version,
        },
        {
            "key": "postgres",
            "label": "PostgreSQL",
            "state": "healthy",
            "detail": f"schema {revision}",
        },
        {
            "key": "embedding",
            "label": "Ollama embedding",
            "state": embedding_state,
            "detail": embedding_error or settings.embedding_model,
        },
        {
            "key": "generation",
            "label": "Ollama knowledge editor",
            "state": generation_state,
            "detail": generation_error or settings.generation_model or "disabled",
        },
        _heartbeat_service(
            db,
            key="worker",
            label="Indexer worker",
            prefixes=("worker-service",),
            now=now,
        ),
        _heartbeat_service(
            db,
            key="watcher",
            label="File watcher",
            prefixes=("watcher-service", "watcher:"),
            now=now,
        ),
        _heartbeat_service(
            db,
            key="reconciler",
            label="Reconciler",
            prefixes=("reconciler:",),
            now=now,
        ),
        _heartbeat_service(
            db,
            key="hook-collector",
            label="Codex hook collector",
            prefixes=("hook-collector",),
            now=now,
        ),
        {
            "key": "gpu-scheduler",
            "label": "Host GPU scheduler",
            "state": gpu_state,
            "detail": gpu_error or "read-only health",
        },
    ]
    if settings.gpu_embedding_reaper_health_enabled:
        portal_services.append(
            load_gpu_embedding_reaper(
                settings.gpu_embedding_reaper_path,
                now=now,
                stale_after_seconds=max(
                    90,
                    settings.docker_inventory_stale_seconds * 2,
                ),
            )
        )
    inventory, docker_groups = load_docker_groups(
        settings.docker_inventory_path,
        now=now,
        stale_after_seconds=settings.docker_inventory_stale_seconds,
    )
    groups = [
        {
            "key": "portal:local-knowledge-portal",
            "category": "portal",
            "project": "local-knowledge-portal",
            "state": "healthy"
            if all(
                item["state"] in {"healthy", "disabled", "idle", "busy"}
                for item in portal_services
            )
            else "error",
            "services": portal_services,
        },
        {
            "key": "ai:workstation-ai",
            "category": "ai",
            "project": "workstation-ai",
            "state": "healthy"
            if comfyui_state == "healthy" and ai_toolkit_state == "healthy"
            else "attention",
            "services": [
                {
                    "key": "comfyui",
                    "label": "ComfyUI",
                    "state": comfyui_state,
                    "detail": comfyui_error or "image generation UI is responding",
                },
                {
                    "key": "ai-toolkit",
                    "label": "AI-Toolkit",
                    "state": ai_toolkit_state,
                    "detail": ai_toolkit_error or "training UI is responding",
                },
            ],
        },
        {
            "key": "infrastructure:host-control",
            "category": "infrastructure",
            "project": "host-control",
            "state": service_manager_state,
            "services": [
                {
                    "key": "service-manager",
                    "label": "Host service manager",
                    "state": service_manager_state,
                    "detail": service_manager_error or "allow-listed control is available",
                }
            ],
        },
        *docker_groups,
    ]
    services = [service for group in groups for service in group["services"]]
    overall = "healthy" if (
        inventory["state"] == "healthy"
        and all(
            item["state"] in {"healthy", "disabled", "running", "idle", "busy"}
            for item in services
        )
    ) else "attention"
    return {
        "overall": overall,
        "checked_at": now,
        "inventory": inventory,
        "groups": groups,
        "services": services,
    }


def _service_manager_request(
    method: Literal["GET", "POST"],
    path: str,
    payload: dict | None = None,
) -> dict:
    if not settings.service_manager_token:
        raise HTTPException(
            503,
            detail={"service": "host-service-manager", "status": "control_not_configured"},
        )
    try:
        response = httpx.request(
            method,
            f"{settings.service_manager_base_url}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {settings.service_manager_token}"},
            timeout=(
                settings.service_manager_action_timeout_seconds
                if method == "POST"
                else settings.service_manager_timeout_seconds
            ),
            follow_redirects=False,
        )
        response.raise_for_status()
        result = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(
            504,
            detail={"service": "host-service-manager", "status": "timeout"},
        ) from exc
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        if upstream_status in {400, 404, 409}:
            try:
                detail = exc.response.json()
            except ValueError:
                detail = {"status": "request_rejected"}
            raise HTTPException(upstream_status, detail=detail) from exc
        raise HTTPException(
            502,
            detail={
                "service": "host-service-manager",
                "status": "upstream_error",
                "upstream_status": upstream_status,
            },
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            503,
            detail={"service": "host-service-manager", "status": "unavailable"},
        ) from exc
    if not isinstance(result, dict):
        raise HTTPException(
            502,
            detail={"service": "host-service-manager", "status": "invalid_response"},
        )
    return result


def _require_service_control_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin not in settings.cors_origin_list:
        raise HTTPException(403, detail="origin is not allowed")


def _issue_service_confirmation(service_id: str, action: str) -> dict[str, object]:
    now = time.monotonic()
    token = secrets.token_urlsafe(32)
    with _SERVICE_CONFIRMATION_LOCK:
        expired = [
            key
            for key, (_, _, expires_at) in _SERVICE_CONFIRMATIONS.items()
            if expires_at <= now
        ]
        for key in expired:
            _SERVICE_CONFIRMATIONS.pop(key, None)
        if len(_SERVICE_CONFIRMATIONS) >= 128:
            oldest = min(
                _SERVICE_CONFIRMATIONS,
                key=lambda key: _SERVICE_CONFIRMATIONS[key][2],
            )
            _SERVICE_CONFIRMATIONS.pop(oldest, None)
        _SERVICE_CONFIRMATIONS[token] = (
            service_id,
            action,
            now + _SERVICE_CONFIRMATION_TTL_SECONDS,
        )
    return {
        "confirmation_token": token,
        "expires_in_seconds": _SERVICE_CONFIRMATION_TTL_SECONDS,
    }


def _consume_service_confirmation(
    token: str,
    service_id: str,
    action: str,
) -> None:
    now = time.monotonic()
    with _SERVICE_CONFIRMATION_LOCK:
 …20907 tokens truncated…    "source_case_id": str(item.source_case_id),
                "target_case_id": str(item.target_case_id),
                "type": item.relation_type,
            }
            for item in relations
        ],
    }


@app.post("/api/v1/knowledge/cases/{case_id}/materialize")
def materialize_knowledge_case(case_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    row = db.get(KnowledgeCase, case_id)
    if not row or row.status != "verified":
        raise HTTPException(404, "verified knowledge case not found")
    path = materialize_case(
        db,
        row,
        vault_dir=settings.vault_dir,
        pipeline_version=settings.pipeline_version,
        content_language=settings.knowledge_content_language,
    )
    db.commit()
    return {
        "case_id": str(row.id),
        "path": str(path),
        "revision": row.metadata_json.get("materialized_revision"),
        "content_hash": row.metadata_json.get("materialized_hash"),
    }


@app.get("/api/v1/tree")
def tree(
    project: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(250, ge=1, le=1000),
    catalog: str = Query("all", pattern="^(all|source|managed)$"),
    db: Session = Depends(get_db),
) -> dict:
    statement = (
        select(Document)
        .join(SourceRoot, SourceRoot.id == Document.source_root_id)
        .where(
            Document.state == DocumentState.active,
            SourceRoot.data_scope == "production",
            _catalog_predicate(catalog),
        )
    )
    if project:
        statement = statement.where(Document.project_key == project)
    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    rows = db.scalars(
        statement.order_by(
            Document.project_key,
            Document.project_relative_path,
            Document.id,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
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
        "page": page,
        "page_size": page_size,
        "total": db.scalar(count_statement),
        "catalog": catalog,
    }


def _elapsed_ms(started_at: datetime | None, ended_at: datetime | None) -> int | None:
    if started_at is None or ended_at is None:
        return None
    return max(0, int((ended_at - started_at).total_seconds() * 1000))


@app.get("/api/v1/jobs")
def jobs(
    status: JobStatus | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    include_rolled_up: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    statement = select(IngestJob)
    count_statement = select(func.count()).select_from(IngestJob)
    if not include_rolled_up:
        visible = or_(
            IngestJob.error_details["retention_state"].astext.is_(None),
            IngestJob.error_details["retention_state"].astext != "rolled_up",
        )
        statement = statement.where(visible)
        count_statement = count_statement.where(visible)
    if status:
        statement = statement.where(IngestJob.status == status)
        count_statement = count_statement.where(IngestJob.status == status)
    rows = db.scalars(
        statement.order_by(IngestJob.created_at.desc(), IngestJob.id.desc())
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
                "available_at": row.available_at,
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "updated_at": row.updated_at,
                "lease_expires_at": row.lease_expires_at,
                "queue_lead_time_ms": _elapsed_ms(row.created_at, row.started_at),
                "processing_duration_ms": _elapsed_ms(
                    row.started_at, row.finished_at
                ),
                "age_ms": _elapsed_ms(row.created_at, datetime.now(timezone.utc)),
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": db.scalar(count_statement),
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


def _worker_payload(db: Session, *, include_retired: bool) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc())).all()
    return [
        {
            "worker_id": row.worker_id,
            "hostname": row.hostname,
            "state": "stale"
            if row.state != "stopped"
            and now - row.last_seen_at > timedelta(seconds=settings.stale_after_seconds)
            else row.state,
            "last_seen_at": row.last_seen_at,
            "current_job_id": row.current_job_id,
            "processed_count": row.processed_count,
            "failed_count": row.failed_count,
            "metadata": row.metadata_json or {},
        }
        for row in rows
        if include_retired or not (row.metadata_json or {}).get("retired", False)
    ]


@app.get("/api/v1/workers")
def workers(
    include_retired: bool = Query(False),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    rows = _worker_payload(db, include_retired=include_retired)
    start = (page - 1) * page_size
    return {
        "items": rows[start : start + page_size],
        "page": page,
        "page_size": page_size,
        "total": len(rows),
    }


@app.get("/api/v1/backups")
def backups(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.scalars(
        select(BackupRun)
        .order_by(BackupRun.created_at.desc(), BackupRun.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "path": row.backup_path,
                "status": row.status,
                "manifest": row.manifest,
                "created_at": row.created_at,
                "finished_at": row.finished_at,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": db.scalar(select(func.count()).select_from(BackupRun)),
    }


@app.get("/api/v1/timeline")
def timeline(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.scalars(
        select(IngestEvent)
        .order_by(IngestEvent.created_at.desc(), IngestEvent.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "event": row.event_type,
                "path": row.path,
                "details": row.details,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": db.scalar(select(func.count()).select_from(IngestEvent)),
    }


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
    succeeded_in_window = (
        db.scalar(
            select(func.count())
            .select_from(IngestJob)
            .where(
                IngestJob.status == JobStatus.succeeded,
                IngestJob.finished_at >= now - timedelta(hours=queue_window_hours),
            )
        )
        or 0
    )
    failed_in_window = (
        db.scalar(
            select(func.count())
            .select_from(IngestJob)
            .where(
                IngestJob.status.in_([JobStatus.failed, JobStatus.dead_letter]),
                IngestJob.finished_at >= now - timedelta(hours=1),
            )
        )
        or 0
    )
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
    current_workers = _worker_payload(db, include_retired=False)
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
                ~Document.extension.in_(sorted(CODE_EXTENSIONS | {".md", ".mdx"})),
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
    embedding_runtime = timeout_circuit_state(
        db,
        threshold=settings.embedding_timeout_circuit_threshold,
        window_seconds=settings.embedding_timeout_circuit_window_seconds,
        runtime_mode=settings.embedding_runtime_mode,
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
        "semantic_coverage": (semantic_chunks / current_chunks if current_chunks else 0.0),
        "embedding_runtime": embedding_runtime,
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
