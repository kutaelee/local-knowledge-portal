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
        issued = _SERVICE_CONFIRMATIONS.pop(token, None)
    if issued is None or issued[0] != service_id or issued[1] != action or issued[2] <= now:
        raise HTTPException(
            409,
            detail={
                "service": "host-service-manager",
                "status": "confirmation_required",
            },
        )


@app.get("/api/v1/service-manager")
def service_manager_status() -> dict:
    return _service_manager_request("GET", "/api/services")


@app.post("/api/v1/service-manager/{service_id}/{action}/confirmation")
def service_manager_confirmation(
    service_id: str,
    action: Literal["start", "stop"],
    request: Request,
) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{1,79}", service_id):
        raise HTTPException(404, detail="unknown service")
    _require_service_control_origin(request)
    return _issue_service_confirmation(service_id, action)


@app.post("/api/v1/service-manager/{service_id}/{action}")
def service_manager_control(
    service_id: str,
    action: Literal["start", "stop"],
    payload: ServiceControlRequest,
    request: Request,
) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{1,79}", service_id):
        raise HTTPException(404, detail="unknown service")
    _require_service_control_origin(request)
    _consume_service_confirmation(
        payload.confirmation_token,
        service_id,
        action,
    )
    return _service_manager_request(
        "POST",
        f"/api/services/{service_id}/{action}",
        {"confirmed": True},
    )


def _gpu_scheduler_get(path: str) -> dict:
    try:
        response = httpx.get(
            f"{settings.gpu_scheduler_base_url}{path}",
            timeout=settings.gpu_scheduler_timeout_seconds,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(
            504,
            detail={"service": "gpu-scheduler", "status": "timeout"},
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            502,
            detail={
                "service": "gpu-scheduler",
                "status": "upstream_error",
                "upstream_status": exc.response.status_code,
            },
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            503,
            detail={"service": "gpu-scheduler", "status": "unavailable"},
        ) from exc
    if not isinstance(payload, dict):
        raise HTTPException(
            502,
            detail={"service": "gpu-scheduler", "status": "invalid_response"},
        )
    return payload


def _gpu_scheduler_control_post(path: str, payload: dict) -> dict:
    """Forward only allow-listed local GPU control calls, server-side.

    The host token never reaches a browser. Control is deliberately disabled
    unless an operator has copied the scheduler token into the portal's ignored
    local environment file.
    """
    if not settings.gpu_scheduler_control_token:
        raise HTTPException(
            503,
            detail={"service": "gpu-scheduler", "status": "control_not_configured"},
        )
    try:
        response = httpx.post(
            f"{settings.gpu_scheduler_base_url}{path}",
            json=payload,
            headers={"X-GPUQ-Token": settings.gpu_scheduler_control_token},
            timeout=settings.gpu_scheduler_timeout_seconds,
            follow_redirects=False,
        )
        response.raise_for_status()
        result = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(
            504,
            detail={"service": "gpu-scheduler", "status": "timeout"},
        ) from exc
    except httpx.HTTPStatusError as exc:
        upstream_status = exc.response.status_code
        if upstream_status in {400, 409}:
            raise HTTPException(
                upstream_status,
                detail={"service": "gpu-scheduler", "status": "request_rejected"},
            ) from exc
        raise HTTPException(
            502,
            detail={
                "service": "gpu-scheduler",
                "status": "upstream_error",
                "upstream_status": upstream_status,
            },
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            503,
            detail={"service": "gpu-scheduler", "status": "unavailable"},
        ) from exc
    if not isinstance(result, dict):
        raise HTTPException(
            502,
            detail={"service": "gpu-scheduler", "status": "invalid_response"},
        )
    return result


def _active_gpu_workload(workload_key: str) -> dict | None:
    """Return a bounded view of a portal-owned GPU reservation."""

    try:
        payload = _gpu_scheduler_get("/api/status")
    except HTTPException:
        return None
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict):
        return None
    for bucket in ("active", "queued"):
        rows = jobs.get(bucket)
        if not isinstance(rows, list):
            continue
        for item in rows:
            if not isinstance(item, dict) or item.get("workload_key") != workload_key:
                continue
            return {
                "id": item.get("id"),
                "status": item.get("status"),
                "bucket": bucket,
                "submitted_at": item.get("submitted_at"),
                "started_at": item.get("started_at"),
                "scheduling_note": item.get("scheduling_note"),
            }
    return None


def _generation_editor_connection(runtime_job: dict | None) -> str:
    if runtime_job is None:
        return "scheduled_idle"
    if runtime_job.get("bucket") == "queued":
        return "waiting_for_gpu"
    try:
        response = httpx.get(
            f"{settings.generation_base_url}/api/tags",
            timeout=settings.gpu_scheduler_timeout_seconds,
            follow_redirects=False,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        return "starting"
    return "connected"


def _comfyui_bridge_status() -> dict:
    """Read a deliberately small local ComfyUI bridge health summary."""
    try:
        response = httpx.get(
            settings.comfyui_bridge_health_url,
            timeout=settings.gpu_scheduler_timeout_seconds,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {"state": "unavailable"}
    if not isinstance(payload, dict):
        return {"state": "invalid_response"}
    process_id = payload.get("process_id")
    return {
        "state": "ready" if payload.get("ready") is True else "unavailable",
        "process_id": process_id if isinstance(process_id, int) else None,
        "reservation_mode": (
            payload.get("reservation_mode")
            if payload.get("reservation_mode") in {"server_managed", "prompt_reservation"}
            else None
        ),
        "requested_vram_mb": (
            payload.get("requested_vram_mb")
            if isinstance(payload.get("requested_vram_mb"), int)
            else None
        ),
    }


def _generation_ollama_workload() -> dict:
    """Return a bounded view of the workstation-wide Ollama runtime."""
    try:
        response = httpx.get(
            f"{settings.generation_base_url}/api/ps",
            timeout=settings.gpu_scheduler_timeout_seconds,
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {
            "key": "windows-ollama-generation",
            "label": "Workstation Ollama",
            "kind": "ollama_host",
            "target": "127.0.0.1:11434",
            "state": "stopped",
            "models": [],
            "can_stop": False,
            "error": None,
        }
    rows = payload.get("models") if isinstance(payload, dict) else None
    models = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        size = item.get("size")
        size_vram = item.get("size_vram")
        context = item.get("context_length")
        models.append(
            {
                "name": item["name"][:160],
                "model_id": str(item.get("digest") or "")[:80],
                "size": (
                    f"{size / (1024**3):.1f} GiB"
                    if isinstance(size, int)
                    else "unknown"
                ),
                "processor": (
                    "100% GPU"
                    if isinstance(size_vram, int) and size_vram > 0
                    else "CPU"
                ),
                "context": str(context) if isinstance(context, int) else "",
                "until": str(item.get("expires_at") or "")[:120],
            }
        )
    return {
        "key": "windows-ollama-generation",
        "label": "Workstation Ollama",
        "kind": "ollama_host",
        "target": "127.0.0.1:11434",
        "state": "active" if models else "idle",
        "models": models,
        "can_stop": bool(models),
        "error": None,
    }


def _stop_generation_ollama() -> dict:
    workload = _generation_ollama_workload()
    errors = []
    for model in workload["models"]:
        try:
            response = httpx.post(
                f"{settings.generation_base_url}/api/generate",
                json={"model": model["name"], "keep_alive": 0},
                timeout=30,
                follow_redirects=False,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            errors.append(f"{model['name']}: {type(exc).__name__}")
    refreshed = _generation_ollama_workload()
    if errors:
        refreshed["state"] = "error"
        refreshed["error"] = "; ".join(errors)[:500]
    return refreshed


@app.get("/api/v1/gpu-queue/health")
def gpu_queue_health() -> dict:
    return _gpu_scheduler_get("/api/health")


@app.get("/api/v1/gpu-queue/status")
def gpu_queue_status() -> dict:
    payload = _gpu_scheduler_get("/api/status")
    runtime = payload.get("runtime")
    if not isinstance(runtime, dict):
        raise HTTPException(
            502,
            detail={"service": "gpu-scheduler", "status": "invalid_response"},
        )
    runtime["comfyui_bridge"] = _comfyui_bridge_status()
    external = runtime.get("external_workloads")
    if not isinstance(external, list):
        external = []
        runtime["external_workloads"] = external
    if not any(
        isinstance(item, dict) and item.get("key") == "windows-ollama-generation"
        for item in external
    ):
        external.append(_generation_ollama_workload())
    return payload


@app.get("/api/v1/gpu-queue/jobs/{job_id}")
def gpu_queue_job(job_id: uuid.UUID) -> dict:
    return _gpu_scheduler_get(f"/api/jobs/{job_id}")


@app.post("/api/v1/gpu-queue/jobs/{job_id}/cancel")
def gpu_queue_cancel(job_id: uuid.UUID) -> dict:
    result = _gpu_scheduler_control_post(f"/api/jobs/{job_id}/cancel", {})
    return {"ok": result.get("ok") is True, "job_id": str(job_id)}


@app.post("/api/v1/gpu-queue/external-workloads/{workload_key}/stop")
def gpu_queue_stop_external_workload(workload_key: str) -> dict:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,119}", workload_key):
        raise HTTPException(422, detail="invalid workload key")
    if workload_key == "windows-ollama-generation":
        workload = _stop_generation_ollama()
        return {"ok": workload.get("state") != "error", "workload": workload}
    result = _gpu_scheduler_control_post(
        f"/api/external-workloads/{workload_key}/stop",
        {},
    )
    workload = result.get("workload")
    if not isinstance(workload, dict):
        raise HTTPException(
            502,
            detail={"service": "gpu-scheduler", "status": "invalid_response"},
        )
    return {"ok": result.get("ok") is True, "workload": workload}


@app.post("/api/v1/gpu-queue/reorder")
def gpu_queue_reorder(request: GpuQueueReorderRequest) -> dict:
    job_ids = [str(job_id) for job_id in request.job_ids]
    if len(job_ids) != len(set(job_ids)):
        raise HTTPException(422, detail="job_ids must be unique")
    result = _gpu_scheduler_control_post("/api/jobs/reorder", {"job_ids": job_ids})
    queue = result.get("queued")
    if not isinstance(queue, list):
        raise HTTPException(
            502,
            detail={"service": "gpu-scheduler", "status": "invalid_response"},
        )
    return {
        "ok": result.get("ok") is True,
        "queued": [
            {"id": item.get("id"), "manual_rank": item.get("manual_rank")}
            for item in queue
            if isinstance(item, dict)
        ],
    }


@app.post("/api/v1/local-llm/hooks/chat", status_code=202)
def local_llm_chat_hook(request: LocalChatCapture) -> dict:
    """Accept a completed local-model turn into a separate atomic spool."""

    event_id, path = spool_local_chat(request, settings.local_llm_spool_dir)
    return {
        "accepted": True,
        "event_id": event_id,
        "source": "local_llm_chat",
        "project": request.project_key,
        "spool": path.parent.name,
        "verification_status": "UNVERIFIED",
    }


_EMBEDDING_REINDEX_WORKLOAD = "local-knowledge-portal-embedding-reindex"


def _embedding_reindex_job(scheduler_status: dict) -> dict | None:
    """Return only the safe, human-facing state of the portal reindex job."""

    jobs = scheduler_status.get("jobs")
    if not isinstance(jobs, dict):
        return None
    # An admitted job takes precedence over a queued retry. Otherwise retain
    # the oldest queued job so its wait is not hidden by a newer submission.
    for bucket in ("active", "queued", "completed"):
        rows = jobs.get(bucket)
        if not isinstance(rows, list):
            continue
        matching = [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("workload_key") == _EMBEDDING_REINDEX_WORKLOAD
        ]
        if not matching:
            continue
        selected = min(matching, key=lambda row: str(row.get("submitted_at") or ""))
        return {
            key: selected.get(key)
            for key in (
                "id",
                "status",
                "submitted_at",
                "started_at",
                "finished_at",
                "requested_vram_mb",
                "estimated_seconds",
                "priority",
                "scheduling_note",
                "error",
            )
        }
    return None


def _embedding_recovery_validation(path: Path) -> dict | None:
    """Read only the bounded, source-text-free GPU recovery proof snapshot."""

    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "invalid"}
    if not isinstance(payload, dict):
        return {"state": "invalid"}
    state = payload.get("state")
    if state not in {"verified", "failed"}:
        return {"state": "invalid"}

    def summary(value: object) -> dict | None:
        if not isinstance(value, dict):
            return None
        return {
            "result_count": value.get("result_count"),
            "vector_result_count": value.get("vector_result_count"),
            "best_similarity": value.get("best_similarity"),
            "provenance_complete": value.get("provenance_complete"),
        }

    return {
        "state": state,
        "checked_at": payload.get("checked_at"),
        "embedding_revision": payload.get("embedding_revision"),
        "reason": payload.get("reason"),
        "error_type": payload.get("error_type"),
        "semantic": summary(payload.get("semantic")),
        "hybrid": summary(payload.get("hybrid")),
    }


def _embedding_recovery_progress(db: Session) -> dict[str, int | str]:
    """Count only current production work remaining for the active revision."""

    deferred = (
        select(Document.id, Document.current_version_id)
        .join(DocumentVersion, Document.current_version_id == DocumentVersion.id)
        .join(SourceRoot, SourceRoot.id == Document.source_root_id)
        .where(
            Document.state == DocumentState.active,
            SourceRoot.data_scope == "production",
            DocumentVersion.metadata_json["embedding_status"].astext == "deferred_runtime",
        )
        .subquery()
    )
    pending_documents = int(db.scalar(select(func.count()).select_from(deferred)) or 0)
    pending_chunks = int(
        db.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .join(deferred, deferred.c.current_version_id == DocumentChunk.document_version_id)
            .outerjoin(
                ChunkEmbedding,
                and_(
                    ChunkEmbedding.chunk_id == DocumentChunk.id,
                    ChunkEmbedding.embedding_revision == settings.embedding_revision,
                ),
            )
            .where(ChunkEmbedding.id.is_(None))
        )
        or 0
    )
    return {
        "pending_documents": pending_documents,
        "pending_chunks": pending_chunks,
        "embedding_revision": settings.embedding_revision,
    }


@app.get("/api/v1/embedding/recovery")
def embedding_recovery_status(db: Session = Depends(get_db)) -> dict:
    """Expose recovery progress without exposing scheduler mutation controls."""

    runtime = timeout_circuit_state(
        db,
        threshold=settings.embedding_timeout_circuit_threshold,
        window_seconds=settings.embedding_timeout_circuit_window_seconds,
        runtime_mode=settings.embedding_runtime_mode,
    )
    scheduler_status = _gpu_scheduler_get("/api/status")
    return {
        "runtime": runtime,
        "reindex": _embedding_reindex_job(scheduler_status),
        "validation": _embedding_recovery_validation(
            settings.runtime_dir / "embedding-recovery-validation.json"
        ),
        "progress": _embedding_recovery_progress(db),
        "scheduler_decision": (scheduler_status.get("runtime") or {}).get("last_decision"),
    }


@app.get("/api/v1/search", response_model=SearchResponse)
def keyword_search(
    q: str = Query(min_length=1, max_length=500),
    top_k: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> SearchResponse:
    response = search(db, SearchRequest(query=q, mode="keyword", top_k=top_k), settings)
    db.commit()
    return _redact_search_response(response)


def _redact_search_response(response: SearchResponse) -> SearchResponse:
    """Ensure historical indexed chunks cannot expose credential-shaped text."""

    for result in response.results:
        result.snippet = redact_text(result.snippet)
        result.title = redact_text(result.title)
        result.heading_or_symbol = redact_text(result.heading_or_symbol or "") or None
    return response


@app.post("/api/v1/search/hybrid", response_model=SearchResponse)
def hybrid_search(request: SearchRequest, db: Session = Depends(get_db)) -> SearchResponse:
    if request.mode in {"semantic", "hybrid"}:
        circuit = timeout_circuit_state(
            db,
            threshold=settings.embedding_timeout_circuit_threshold,
            window_seconds=settings.embedding_timeout_circuit_window_seconds,
            runtime_mode=settings.embedding_runtime_mode,
        )
        if circuit["open"]:
            response = search(db, request.model_copy(update={"mode": "keyword"}), settings)
            response.mode = f"{request.mode}-degraded-keyword-only"
            response.confidence = "low" if response.results else "none"
            db.commit()
            return _redact_search_response(response)
    embedder = _embedder() if request.mode in {"semantic", "hybrid"} else None
    try:
        response = search(db, request, settings, embedder)
    except httpx.HTTPError:
        response = search(db, request.model_copy(update={"mode": "keyword"}), settings)
        response.mode = f"{request.mode}-degraded-keyword-only"
        response.confidence = "low" if response.results else "none"
    db.commit()
    return _redact_search_response(response)


@app.get("/api/v1/search/facets")
def search_facets(db: Session = Depends(get_db)) -> dict:
    active = (
        select(Document.id, Document.project_key)
        .join(SourceRoot, SourceRoot.id == Document.source_root_id)
        .where(
            Document.state == DocumentState.active,
            SourceRoot.data_scope == "production",
        )
        .subquery()
    )
    projects = db.execute(
        select(active.c.project_key, func.count())
        .where(active.c.project_key.is_not(None))
        .group_by(active.c.project_key)
        .order_by(func.count().desc(), active.c.project_key)
        .limit(200)
    ).all()
    tags = db.execute(
        select(Tag.name, func.count(func.distinct(DocumentTag.document_id)))
        .join(DocumentTag, DocumentTag.tag_id == Tag.id)
        .join(active, active.c.id == DocumentTag.document_id)
        .group_by(Tag.name)
        .order_by(func.count(func.distinct(DocumentTag.document_id)).desc(), Tag.name)
        .limit(200)
    ).all()
    return {
        "projects": [{"name": name, "count": count} for name, count in projects],
        "tags": [{"name": name, "count": count} for name, count in tags],
    }


@app.post("/api/v1/rag/context")
def rag_context(request: RagRequest, db: Session = Depends(get_db)) -> dict:
    search_request = SearchRequest(
        query=request.query,
        mode="hybrid",
        top_k=request.top_k,
        project=request.filters.get("project"),
        tags=request.filters.get("tags") or [],
        tag_mode=request.filters.get("tag_mode") or "all",
        path_prefix=request.filters.get("path_prefix"),
    )
    response = hybrid_search(search_request, db)
    contexts = build_expanded_context(
        db,
        response.results,
        max_chars=request.max_chars,
    )
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
def document_detail(
    document_id: uuid.UUID,
    chunk_page: int = Query(1, ge=1),
    chunk_page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(Document, document_id)
    if not row:
        raise HTTPException(404, "document not found")
    chunks = db.scalars(
        select(DocumentChunk)
        .where(DocumentChunk.document_version_id == row.current_version_id)
        .order_by(DocumentChunk.chunk_index)
        .offset((chunk_page - 1) * chunk_page_size)
        .limit(chunk_page_size)
    ).all()
    return {
        **_document_json(row),
        "chunk_page": chunk_page,
        "chunk_page_size": chunk_page_size,
        "chunk_total": db.scalar(
            select(func.count())
            .select_from(DocumentChunk)
            .where(DocumentChunk.document_version_id == row.current_version_id)
        ),
        "chunks": [
            {
                "id": str(chunk.id),
                "index": chunk.chunk_index,
                "type": chunk.chunk_type,
                "heading": chunk.heading_path,
                "symbol": chunk.symbol_name,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "content": redact_text(chunk.content),
                "metadata": _display_metadata(chunk.metadata_json),
            }
            for chunk in chunks
        ],
    }


@app.get("/api/v1/documents/{document_id}/versions")
def versions(
    document_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=2, le=100),
    db: Session = Depends(get_db),
) -> dict:
    rows = db.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.detected_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "content_hash": row.content_hash,
                "change_type": row.change_type,
                "detected_at": row.detected_at,
                "line_count": row.line_count,
                "parser_version": row.parser_version,
                "chunker_version": row.chunker_version,
                "metadata": _display_metadata(row.metadata_json),
                "diff_summary": redact_value(row.diff_summary),
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": db.scalar(
            select(func.count())
            .select_from(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
        ),
    }


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
            lines.setdefault(offset, redact_text(value))
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
def backlinks(
    document_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    predicate = DocumentLink.target_document_id == document_id
    total = int(db.scalar(select(func.count()).select_from(DocumentLink).where(predicate)) or 0)
    rows = db.execute(
        select(DocumentLink, Document)
        .join(Document, Document.id == DocumentLink.source_document_id)
        .where(predicate)
        .order_by(Document.relative_path, DocumentLink.source_line)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {
                "source_document_id": str(source.id),
                "source_path": source.relative_path,
                "source_filename": source.filename,
                "line": link.source_line,
                "type": link.link_type,
                "raw_target": link.raw_target,
            }
            for link, source in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.get("/api/v1/documents/{document_id}/links")
def outgoing_links(
    document_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    predicate = DocumentLink.source_document_id == document_id
    total = int(db.scalar(select(func.count()).select_from(DocumentLink).where(predicate)) or 0)
    rows = db.scalars(
        select(DocumentLink)
        .where(predicate)
        .order_by(DocumentLink.source_line, DocumentLink.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    target_ids = [row.target_document_id for row in rows if row.target_document_id]
    targets = {
        row.id: row
        for row in db.scalars(select(Document).where(Document.id.in_(target_ids)))
    } if target_ids else {}
    return {
        "items": [
            {
                "target_document_id": str(row.target_document_id)
                if row.target_document_id
                else None,
                "target_path": targets[row.target_document_id].relative_path
                if row.target_document_id in targets
                else None,
                "raw_target": row.raw_target,
                "line": row.source_line,
                "type": row.link_type,
                "resolution": "resolved" if row.target_document_id else "unresolved",
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.get("/api/v1/graph/facets")
def document_graph_facets(db: Session = Depends(get_db)) -> dict:
    source = aliased(Document)
    rows = db.execute(
        select(
            source.project_key,
            func.count(DocumentLink.id),
            func.count(DocumentLink.target_document_id),
        )
        .join(source, source.id == DocumentLink.source_document_id)
        .join(SourceRoot, SourceRoot.id == source.source_root_id)
        .where(
            source.state == DocumentState.active,
            SourceRoot.data_scope == "production",
        )
        .group_by(source.project_key)
        .order_by(func.count(DocumentLink.id).desc(), source.project_key)
        .limit(100)
    ).all()
    return {
        "projects": [
            {
                "key": project,
                "links": int(total),
                "resolved": int(resolved),
                "unresolved": int(total - resolved),
            }
            for project, total, resolved in rows
        ],
        "basis": {
            "wikilink": "explicit Obsidian [[target]] links",
            "markdown": "explicit relative Markdown links",
            "semantic_similarity": False,
            "code_imports": False,
        },
    }


@app.get("/api/v1/graph")
def document_graph(
    project: str | None = None,
    link_type: Literal["all", "wikilink", "markdown"] = "all",
    resolution: Literal["resolved", "unresolved", "all"] = "resolved",
    limit: int = Query(60, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    source = aliased(Document)
    target = aliased(Document)
    statement = (
        select(DocumentLink, source, target)
        .join(source, source.id == DocumentLink.source_document_id)
        .outerjoin(target, target.id == DocumentLink.target_document_id)
        .where(source.state == DocumentState.active)
        .order_by(source.relative_path, DocumentLink.source_line, DocumentLink.id)
        .limit(limit + 1)
    )
    if project:
        statement = statement.where(
            or_(source.project_key == project, target.project_key == project)
        )
    if link_type != "all":
        statement = statement.where(DocumentLink.link_type == link_type)
    if resolution == "resolved":
        statement = statement.where(DocumentLink.target_document_id.is_not(None))
    elif resolution == "unresolved":
        statement = statement.where(DocumentLink.target_document_id.is_(None))
    all_rows = db.execute(statement).all()
    truncated = len(all_rows) > limit
    rows = all_rows[:limit]
    nodes: dict[str, dict] = {}
    edges = []
    for link, source_document, target_document in rows:
        source_id = str(source_document.id)
        nodes[source_id] = {
            "id": source_id,
            "label": source_document.filename,
            "path": source_document.relative_path,
            "project": source_document.project_key,
            "state": "resolved",
        }
        if target_document is None:
            target_id = f"unresolved:{hashlib.sha256(link.raw_target.encode()).hexdigest()[:16]}"
            nodes[target_id] = {
                "id": target_id,
                "label": link.raw_target,
                "path": None,
                "project": source_document.project_key,
                "state": "unresolved",
            }
        else:
            target_id = str(target_document.id)
            nodes[target_id] = {
                "id": target_id,
                "label": target_document.filename,
                "path": target_document.relative_path,
                "project": target_document.project_key,
                "state": "resolved",
            }
        edges.append(
            {
                "id": str(link.id),
                "source": source_id,
                "target": target_id,
                "type": link.link_type,
                "line": link.source_line,
                "raw_target": link.raw_target,
                "resolved": target_document is not None,
            }
        )
    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "project": project,
        "link_type": link_type,
        "resolution": resolution,
        "limit": limit,
        "truncated": truncated,
        "summary": {
            "visible_links": len(edges),
            "visible_documents": len(nodes),
            "resolved_links": sum(1 for edge in edges if edge["resolved"]),
            "unresolved_links": sum(1 for edge in edges if not edge["resolved"]),
        },
    }


@app.get("/api/v1/projects")
def projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    catalog: str = Query("all", pattern="^(all|source|managed)$"),
    db: Session = Depends(get_db),
) -> dict:
    grouped = (
        select(
            Document.project_key,
            func.count(Document.id).label("document_count"),
        )
        .join(SourceRoot, SourceRoot.id == Document.source_root_id)
        .where(
            Document.state == DocumentState.active,
            SourceRoot.data_scope == "production",
            _catalog_predicate(catalog),
        )
        .group_by(Document.project_key)
        .subquery()
    )
    total, document_total = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(grouped.c.document_count), 0),
        ).select_from(grouped)
    ).one()
    rows = db.execute(
        select(grouped.c.project_key, grouped.c.document_count)
        .order_by(grouped.c.document_count.desc(), grouped.c.project_key)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [
            {"key": name, "document_count": int(count)}
            for name, count in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": int(total),
        "document_total": int(document_total),
        "catalog": catalog,
    }


def _display_metadata(metadata: dict | None, *, hide_journal_history: bool = False) -> dict:
    """Provide a redacted metadata copy without altering immutable activity rows."""

    safe = redact_value(metadata or {})
    if not isinstance(safe, dict):
        return {}
    if hide_journal_history:
        # v1 is intentionally retained in derived DB metadata only for audit;
        # it can contain the pre-redaction presentation and is never a UI field.
        safe.pop("journal_presentation_v1", None)
    return safe


def _activity_json(row: ActivityEvent) -> dict:
    activity_source = (
        "local_llm_chat"
        if row.event_type == "LocalChat"
        else str((row.metadata_json or {}).get("activity_source") or "codex")
    )
    return {
        "id": str(row.id),
        "event_key": row.event_key,
        "session_id": row.session_id,
        "turn_id": row.turn_id,
        "event_type": row.event_type,
        "source": activity_source,
        "model": (row.metadata_json or {}).get("model"),
        "occurred_at": row.occurred_at,
        "project": row.project_key,
        "cwd": row.cwd,
        "instruction": redact_text(row.instruction or "") or None,
        "tool_name": row.tool_name,
        "command": redact_text(row.command or "") or None,
        "exit_code": row.exit_code,
        "changed_files": row.changed_files,
        "document_version_ids": [str(value) for value in row.document_version_ids],
        "reported_result": redact_text(row.reported_result or "") or None,
        "verified_result": redact_text(row.verified_result or "") or None,
        "verification_status": row.verification_status,
        "metadata": _display_metadata(row.metadata_json),
    }


@app.get("/api/v1/activities")
def activities(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    event_type: str | None = None,
    verification_status: str | None = None,
    source: Literal["all", "codex", "local_llm_chat"] = "all",
    include_rolled_up: bool = False,
    db: Session = Depends(get_db),
) -> dict:
    statement = select(ActivityEvent)
    count_statement = select(func.count()).select_from(ActivityEvent)
    if source == "local_llm_chat":
        statement = statement.where(ActivityEvent.event_type == "LocalChat")
        count_statement = count_statement.where(ActivityEvent.event_type == "LocalChat")
    elif source == "codex":
        statement = statement.where(ActivityEvent.event_type != "LocalChat")
        count_statement = count_statement.where(ActivityEvent.event_type != "LocalChat")
    if not include_rolled_up:
        visible = or_(
            ActivityEvent.metadata_json["retention_state"].astext.is_(None),
            ActivityEvent.metadata_json["retention_state"].astext != "rolled_up",
        )
        statement = statement.where(visible)
        count_statement = count_statement.where(visible)
    if event_type:
        statement = statement.where(ActivityEvent.event_type == event_type)
        count_statement = count_statement.where(ActivityEvent.event_type == event_type)
    if verification_status:
        statement = statement.where(ActivityEvent.verification_status == verification_status)
        count_statement = count_statement.where(
            ActivityEvent.verification_status == verification_status
        )
    rows = db.scalars(
        statement.order_by(ActivityEvent.occurred_at.desc(), ActivityEvent.id.desc())
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
        "claim": redact_text(row.claim or ""),
        "locator": redact_text(row.locator or "") or None,
        "reported_value": redact_text(row.reported_value or "") or None,
        "verified_value": redact_text(row.verified_value or "") or None,
        "exit_code": row.exit_code,
        "verified": row.verified,
        "metadata": _display_metadata(row.metadata_json),
        "created_at": row.created_at,
    }


def _candidate_json(row: KnowledgeCandidate) -> dict:
    metadata = _display_metadata(row.metadata_json)
    return {
        "id": str(row.id),
        "category": row.category,
        "title": redact_text(row.title),
        "problem": redact_text(row.problem),
        "symptom": redact_text(row.symptom),
        "root_cause": redact_text(row.root_cause),
        "solution": redact_text(row.solution),
        "status": row.status,
        "evidence_gate_status": row.evidence_gate_status,
        "reported_result": redact_text(row.reported_result or "") or None,
        "verified_result": redact_text(row.verified_result or "") or None,
        "project": metadata.get("project") or "unassigned",
        "metadata": metadata,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _journal_work_type(row: ProjectJournalEntry) -> str:
    configured = (row.metadata_json or {}).get("work_type")
    if configured in {
        "error_resolution",
        "implementation",
        "performance",
        "operations",
        "custom_success",
    }:
        return configured
    if row.failures_json:
        return "error_resolution"
    searchable = f"{row.title} {row.change_summary}".casefold()
    if any(
        token in searchable
        for token in ("cpu", "latency", "performance", "load", "성능", "부하", "지연")
    ):
        return "performance"
    if "operational_or_configuration_change" in row.significance_reasons:
        return "operations"
    return "implementation"


def _journal_category_expression():
    searchable = func.lower(
        ProjectJournalEntry.title
        + " "
        + ProjectJournalEntry.change_summary
    )
    return func.coalesce(
        ProjectJournalEntry.metadata_json["work_type"].astext,
        case(
            (
                func.jsonb_array_length(ProjectJournalEntry.failures_json) > 0,
                "error_resolution",
            ),
            (
                searchable.op("~")(
                    "cpu|latency|performance|load|성능|부하|지연"
                ),
                "performance",
            ),
            (
                ProjectJournalEntry.significance_reasons.contains(
                    ["operational_or_configuration_change"]
                ),
                "operations",
            ),
            else_="implementation",
        ),
    )


def _journal_json(row: ProjectJournalEntry) -> dict:
    return {
        "id": str(row.id),
        "source_stop_activity_id": str(row.source_stop_activity_id),
        "project": row.project_key,
        "category": _journal_work_type(row),
        "occurred_at": row.occurred_at,
        "title": redact_text(row.title),
        "intent": redact_text(row.intent),
        "change_summary": redact_text(row.change_summary),
        "failures": redact_value(row.failures_json),
        "resolution": redact_text(row.resolution),
        "verification": redact_value(row.verification_json),
        "changed_files": row.changed_files,
        "knowledge_references": row.knowledge_references_json,
        "significance_reasons": row.significance_reasons,
        "verification_status": row.verification_status,
        "metadata": _display_metadata(row.metadata_json, hide_journal_history=True),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _current_project_journal(
    rows: list[ProjectJournalEntry],
) -> tuple[list[dict], list[dict]]:
    """Build a current-state document from the newest source per topic."""

    selected: dict[str, ProjectJournalEntry] = {}
    for row in rows:
        selected.setdefault(_journal_work_type(row), row)
    current_sources = sorted(
        selected.items(),
        key=lambda item: (item[1].occurred_at, str(item[1].id)),
        reverse=True,
    )
    section_titles = {
        "implementation": {"ko": "현재 구현", "en": "Current implementation"},
        "operations": {"ko": "운영과 설정", "en": "Operations and configuration"},
        "performance": {"ko": "성능과 안정성", "en": "Performance and reliability"},
        "error_resolution": {"ko": "오류 해결", "en": "Resolved issues"},
    }
    sections: list[dict] = []
    sources: list[dict] = []
    for citation_number, (category, row) in enumerate(current_sources, start=1):
        sections.append(
            {
                "id": category,
                "category": category,
                "title": section_titles.get(
                    category,
                    {"ko": "현재 상태", "en": "Current state"},
                ),
                "updated_at": row.occurred_at,
                "content": redact_text(row.change_summary),
                "verification_status": row.verification_status,
                "citation_numbers": [citation_number],
            }
        )
        sources.append(
            {
                "citation_number": citation_number,
                "entry": _journal_json(row),
            }
        )
    return sections, sources


@app.get("/api/v1/project-journal")
def project_journal(
    project: str | None = None,
    category: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    statement = select(ProjectJournalEntry)
    count_statement = select(func.count()).select_from(ProjectJournalEntry)
    if project:
        statement = statement.where(ProjectJournalEntry.project_key == project)
        count_statement = count_statement.where(ProjectJournalEntry.project_key == project)
    if category:
        category_expression = _journal_category_expression()
        statement = statement.where(category_expression == category)
        count_statement = count_statement.where(category_expression == category)
    rows = db.scalars(
        statement.order_by(
            ProjectJournalEntry.occurred_at.desc(),
            ProjectJournalEntry.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_journal_json(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": db.scalar(count_statement),
    }


def _developer_feed_json(row: DeveloperFeedPost, language: str) -> dict:
    return {
        "id": str(row.id),
        "project": row.project_key,
        "post_type": row.post_type,
        "thread_root_id": str(row.thread_root_id) if row.thread_root_id else None,
        "reply_to_id": str(row.reply_to_id) if row.reply_to_id else None,
        "sequence": row.sequence,
        "content": row.content_en if language == "en" else row.content_ko,
        "content_ko": row.content_ko,
        "content_en": row.content_en,
        "source_manifest": row.source_manifest_json,
        "source_embedding_from": row.source_embedding_from,
        "source_embedding_to": row.source_embedding_to,
        "persona_version": row.persona_version,
        "created_at": row.created_at,
    }


@app.get("/api/v1/developer-feed")
def developer_feed(
    project: str | None = None,
    language: Literal["ko", "en"] = "ko",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    statement = select(DeveloperFeedPost)
    count_statement = select(func.count()).select_from(DeveloperFeedPost)
    if project:
        statement = statement.where(DeveloperFeedPost.project_key == project)
        count_statement = count_statement.where(
            DeveloperFeedPost.project_key == project
        )
    rows = db.scalars(
        statement.order_by(
            DeveloperFeedPost.created_at.desc(),
            DeveloperFeedPost.thread_root_id.desc(),
            DeveloperFeedPost.sequence,
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_developer_feed_json(row, language) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": db.scalar(count_statement),
        "language": language,
    }


@app.get("/api/v1/developer-feed/status")
def developer_feed_status(db: Session = Depends(get_db)) -> dict:
    latest_activity = db.scalar(
        select(func.max(DeveloperFeedPost.created_at)).where(
            DeveloperFeedPost.post_type == "activity"
        )
    )
    latest_daily = db.scalar(
        select(func.max(DeveloperFeedPost.created_at)).where(
            DeveloperFeedPost.post_type == "daily_summary"
        )
    )
    local_tz = ZoneInfo(settings.developer_feed_timezone)
    local_now = datetime.now(timezone.utc).astimezone(local_tz)
    next_daily = local_now.replace(
        hour=settings.developer_feed_daily_hour,
        minute=0,
        second=0,
        microsecond=0,
    )
    if next_daily <= local_now:
        next_daily += timedelta(days=1)
    return {
        "enabled": settings.developer_feed_enabled,
        "timezone": settings.developer_feed_timezone,
        "daily_hour": settings.developer_feed_daily_hour,
        "next_daily_at": next_daily,
        "latest_activity_at": latest_activity,
        "latest_daily_at": latest_daily,
        "embedding_revision": settings.embedding_revision,
        "persona_version": settings.developer_feed_persona_version,
    }


@app.get("/api/v1/project-journal/projects")
def project_journal_projects(
    project: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    journal_statement = (
        select(
            ProjectJournalEntry.project_key.label("project"),
            func.count(ProjectJournalEntry.id).label("entry_count"),
            func.max(ProjectJournalEntry.occurred_at).label("latest_at"),
            func.bool_and(
                ProjectJournalEntry.verification_status == "VERIFIED"
            ).label("all_verified"),
        )
        .where(ProjectJournalEntry.verification_status != "OUT_OF_PROJECT_SCOPE")
        .group_by(ProjectJournalEntry.project_key)
        .order_by(
            func.max(ProjectJournalEntry.occurred_at).desc(),
            ProjectJournalEntry.project_key,
        )
    )
    document_statement = (
        select(
            Document.project_key.label("project"),
            func.count(Document.id).label("document_count"),
            func.max(Document.modified_at_fs).label("latest_at"),
        )
        .where(
            Document.state == DocumentState.active,
            Document.project_key.is_not(None),
            Document.project_key != "",
            Document.extension.in_([".md", ".mdx", ".txt", ".rst"]),
            not_(Document.relative_path.startswith("_generated/")),
        )
        .group_by(Document.project_key)
    )
    if project:
        journal_statement = journal_statement.where(
            ProjectJournalEntry.project_key == project
        )
        document_statement = document_statement.where(Document.project_key == project)
    projects_by_name: dict[str, dict] = {}
    for row in db.execute(journal_statement):
        projects_by_name[row.project] = {
            "project": row.project,
            "entry_count": int(row.entry_count),
            "document_count": 0,
            "latest_at": row.latest_at,
            "all_verified": bool(row.all_verified),
        }
    for row in db.execute(document_statement):
        current = projects_by_name.setdefault(
            row.project,
            {
                "project": row.project,
                "entry_count": 0,
                "document_count": 0,
                "latest_at": row.latest_at,
                "all_verified": False,
            },
        )
        current["document_count"] = int(row.document_count)
        if current["latest_at"] is None or (
            row.latest_at is not None and row.latest_at > current["latest_at"]
        ):
            current["latest_at"] = row.latest_at
    ordered_projects = sorted(
        projects_by_name.values(),
        key=lambda item: (
            item["latest_at"] or datetime.min.replace(tzinfo=timezone.utc),
            item["project"],
        ),
        reverse=True,
    )
    rows = ordered_projects[(page - 1) * page_size : page * page_size]
    project_names = [row["project"] for row in rows]
    refresh_plan = {
        item.project: item
        for item in project_article_plan(db, settings)
        if item.project in project_names
    }
    article_rows = (
        list(
            db.scalars(
                select(ProjectArticle).where(
                    ProjectArticle.project_key.in_(project_names)
                )
            )
        )
        if project_names
        else []
    )
    articles = {article.project_key: article for article in article_rows}
    revision_ids = [
        article.current_revision_id
        for article in article_rows
        if article.current_revision_id is not None
    ]
    revisions = {
        revision.id: revision
        for revision in db.scalars(
            select(ProjectArticleRevision).where(
                ProjectArticleRevision.id.in_(revision_ids)
            )
        )
    }
    return {
        "items": [
            {
                "id": row["project"],
                "project": row["project"],
                "title": (
                    revisions[articles[row["project"]].current_revision_id].title
                    if row["project"] in articles
                    and articles[row["project"]].current_revision_id in revisions
                    else (
                        f"{row['project']} 프로젝트 문서"
                        if settings.knowledge_content_language == "ko"
                        else f"{row['project']} project article"
                    )
                ),
                "entry_count": row["entry_count"],
                "document_count": row["document_count"],
                "latest_at": row["latest_at"],
                "verification_status": (
                    "CITED"
                    if row["project"] in articles
                    and articles[row["project"]].current_revision_id in revisions
                    else ("VERIFIED" if row["all_verified"] else "UNVERIFIED")
                ),
                "article_status": (
                    _project_article_display_status(
                        articles.get(row["project"]),
                        (
                            refresh_plan[row["project"]].due_reason
                            if row["project"] in refresh_plan
                            else None
                        ),
                    )
                ),
                "article_due_reason": (
                    refresh_plan[row["project"]].due_reason
                    if row["project"] in refresh_plan
                    else None
                ),
                "revision_number": (
                    revisions[
                        articles[row["project"]].current_revision_id
                    ].revision_number
                    if row["project"] in articles
                    and articles[row["project"]].current_revision_id in revisions
                    else None
                ),
                "last_compared_at": (
                    articles[row["project"]].last_compared_at
                    if row["project"] in articles
                    else None
                ),
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": len(ordered_projects),
    }


@app.get("/api/v1/project-journal/projects/{project}")
def project_journal_project_detail(
    project: str,
    db: Session = Depends(get_db),
) -> dict:
    total = db.scalar(
        select(func.count())
        .select_from(ProjectJournalEntry)
        .where(ProjectJournalEntry.project_key == project)
    ) or 0
    document_count, document_latest_at = db.execute(
        select(func.count(Document.id), func.max(Document.modified_at_fs)).where(
            Document.project_key == project,
            Document.state == DocumentState.active,
            Document.extension.in_([".md", ".mdx", ".txt", ".rst"]),
            not_(Document.relative_path.startswith("_generated/")),
        )
    ).one()
    article = db.scalar(
        select(ProjectArticle).where(ProjectArticle.project_key == project)
    )
    refresh_item = next(
        (
            item
            for item in project_article_plan(db, settings)
            if item.project == project
        ),
        None,
    )
    article_status = _project_article_display_status(
        article,
        refresh_item.due_reason if refresh_item is not None else None,
    )
    revision = (
        db.get(ProjectArticleRevision, article.current_revision_id)
        if article is not None and article.current_revision_id
        else None
    )
    if revision is not None:
        return {
            "id": project,
            "project": project,
            "title": redact_text(revision.title),
            "entry_count": total,
            "document_count": int(document_count),
            "visible_entry_count": len(revision.sources_json),
            "latest_at": revision.created_at,
            "verification_status": "CITED",
            "article_status": article_status,
            "article_due_reason": (
                refresh_item.due_reason if refresh_item is not None else None
            ),
            "last_compared_at": article.last_compared_at,
            "content_type": "canonical_article",
            "revision_number": revision.revision_number,
            "previous_revision_id": (
                str(revision.previous_revision_id)
                if revision.previous_revision_id
                else None
            ),
            "prompt_version": revision.prompt_version,
            "model": revision.model,
            "model_digest": revision.model_digest,
            "standfirst": redact_value(revision.standfirst_json),
            "sections": redact_value(revision.sections_json),
            "sources": redact_value(revision.sources_json),
            "change_summary": revision.change_summary_json,
            "truncated": False,
        }
    if not total and not document_count:
        raise HTTPException(404, "project article source not found")
    if not total:
        return {
            "id": project,
            "project": project,
            "title": (
                f"{project} 프로젝트 문서"
                if settings.knowledge_content_language == "ko"
                else f"{project} project article"
            ),
            "entry_count": 0,
            "document_count": int(document_count),
            "visible_entry_count": 0,
            "latest_at": document_latest_at,
            "verification_status": "UNVERIFIED",
            "article_status": article_status,
            "article_due_reason": (
                refresh_item.due_reason if refresh_item is not None else None
            ),
            "last_compared_at": (
                article.last_compared_at if article is not None else None
            ),
            "content_type": "legacy_extract",
            "revision_number": None,
            "truncated": False,
            "sections": [],
            "sources": [],
        }
    rows = list(
        db.scalars(
            select(ProjectJournalEntry)
            .where(ProjectJournalEntry.project_key == project)
            .order_by(
                ProjectJournalEntry.occurred_at.desc(),
                ProjectJournalEntry.id.desc(),
            )
            .limit(200)
        )
    )
    sections, sources = _current_project_journal(rows)
    return {
        "id": project,
        "project": project,
        "title": (
            f"{project} 개발 일지"
            if settings.knowledge_content_language == "ko"
            else f"{project} development journal"
        ),
        "entry_count": total,
        "document_count": int(document_count),
        "visible_entry_count": len(rows),
        "latest_at": rows[0].occurred_at,
        "verification_status": "VERIFIED"
        if all(row.verification_status == "VERIFIED" for row in rows)
        else "UNVERIFIED",
        "article_status": article_status,
        "article_due_reason": (
            refresh_item.due_reason if refresh_item is not None else None
        ),
        "last_compared_at": (
            article.last_compared_at if article is not None else None
        ),
        "content_type": "legacy_extract",
        "revision_number": None,
        "truncated": total > len(rows),
        "sections": sections,
        "sources": sources,
    }


@app.get("/api/v1/project-journal/projects/{project}/versions")
def project_article_versions(
    project: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict:
    article = db.scalar(
        select(ProjectArticle).where(ProjectArticle.project_key == project)
    )
    if article is None:
        return {"items": [], "page": page, "page_size": page_size, "total": 0}
    statement = (
        select(ProjectArticleRevision)
        .where(ProjectArticleRevision.article_id == article.id)
        .order_by(
            ProjectArticleRevision.revision_number.desc(),
            ProjectArticleRevision.created_at.desc(),
        )
    )
    rows = list(
        db.scalars(
            statement.offset((page - 1) * page_size).limit(page_size)
        )
    )
    total = int(
        db.scalar(
            select(func.count())
            .select_from(ProjectArticleRevision)
            .where(ProjectArticleRevision.article_id == article.id)
        )
        or 0
    )
    return {
        "items": [
            {
                "id": str(row.id),
                "revision_number": row.revision_number,
                "title": redact_text(row.title),
                "created_at": row.created_at,
                "source_hash": row.source_hash,
                "change_summary": row.change_summary_json,
                "model": row.model,
                "model_digest": row.model_digest,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@app.get("/api/v1/project-journal/{entry_id}")
def project_journal_detail(
    entry_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(ProjectJournalEntry, entry_id)
    if not row:
        raise HTTPException(404, "project journal entry not found")
    return _journal_json(row)


@app.get("/api/v1/knowledge/candidates")
def candidates(
    status: str | None = None,
    project: str | None = None,
    category: str | None = None,
    review_only: bool = False,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    statement = select(KnowledgeCandidate)
    count_statement = select(func.count()).select_from(KnowledgeCandidate)
    if status:
        statement = statement.where(KnowledgeCandidate.status == status)
        count_statement = count_statement.where(KnowledgeCandidate.status == status)
    if review_only:
        visible = KnowledgeCandidate.status.not_in(["published", "activity_only"])
        statement = statement.where(visible)
        count_statement = count_statement.where(visible)
    if project:
        project_match = KnowledgeCandidate.metadata_json["project"].astext == project
        statement = statement.where(project_match)
        count_statement = count_statement.where(project_match)
    if category:
        statement = statement.where(KnowledgeCandidate.category == category)
        count_statement = count_statement.where(KnowledgeCandidate.category == category)
    rows = db.scalars(
        statement.order_by(
            KnowledgeCandidate.created_at.desc(),
            KnowledgeCandidate.id.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_candidate_json(row) for row in rows],
        "page": page,
        "total": db.scalar(count_statement),
    }


@app.get("/api/v1/knowledge/curation/status")
def knowledge_curation_status(db: Session = Depends(get_db)) -> dict:
    scheduler = db.get(SystemSetting, "knowledge_curator.scheduler")
    state = dict(scheduler.value or {}) if scheduler else {"state": "not_started"}
    runtime_job = _active_gpu_workload(_CURATION_WORKLOAD)
    editor_connection = _generation_editor_connection(runtime_job)
    if runtime_job is not None:
        state["runtime_job"] = runtime_job
        state["state"] = (
            "running" if runtime_job.get("bucket") == "active" else "waiting_for_gpu"
        )
    # Keep the host-side scheduler from reserving a GPU only to discover that
    # every candidate is either already held, published, or awaiting semantic
    # deduplication.  This mirrors the curator's eligible statuses and leaves
    # the separate GPU vector-dedup worker as the owner of pending matches.
    eligible_candidates = int(
        db.scalar(
            select(func.count())
            .select_from(KnowledgeCandidate)
            .where(
                KnowledgeCandidate.status.in_(["candidate", "verified", "needs_review"]),
                KnowledgeCandidate.evidence_gate_status == "VERIFIED",
                or_(
                    KnowledgeCandidate.status != "needs_review",
                    and_(
                        func.coalesce(
                            KnowledgeCandidate.metadata_json["journal_backed"].astext,
                            "false",
                        )
                        == "true",
                        or_(
                            func.coalesce(
                                KnowledgeCandidate.metadata_json[
                                    "journal_reassessment"
                                ]["version"].astext,
                                "",
                            )
                            != "",
                            func.coalesce(
                                KnowledgeCandidate.metadata_json["extractor"].astext,
                                "",
                            )
                            == "deterministic-activity-v2",
                        ),
                        func.coalesce(
                            KnowledgeCandidate.metadata_json["curation"][
                                "harness_version"
                            ].astext,
                            "",
                        )
                        != CURATION_HARNESS_VERSION,
                    ),
                ),
                func.coalesce(
                    KnowledgeCandidate.metadata_json["semantic_dedup"]["state"].astext,
                    "",
                )
                != "pending_gpu_vector_check",
            )
        )
        or 0
    )
    qualification = None
    qualification_key = state.get("qualification_key")
    if isinstance(qualification_key, str):
        row = db.get(SystemSetting, qualification_key)
        qualification = dict(row.value or {}) if row else None
    record_summary = {
        "verified_cases": int(
            db.scalar(
                select(func.count())
                .select_from(KnowledgeCase)
                .where(KnowledgeCase.status == "verified")
            )
            or 0
        ),
        "project_journals": int(
            db.scalar(select(func.count()).select_from(ProjectJournalEntry)) or 0
        ),
        "editorial_candidates": int(
            db.scalar(
                select(func.count())
                .select_from(KnowledgeCandidate)
                .where(KnowledgeCandidate.status.in_(["candidate", "verified", "needs_review"]))
            )
            or 0
        ),
        "activity_only": int(
            db.scalar(
                select(func.count())
                .select_from(KnowledgeCandidate)
                .where(KnowledgeCandidate.status == "activity_only")
            )
            or 0
        ),
    }
    return {
        "enabled": settings.knowledge_curation_enabled,
        "project_article_enabled": settings.project_article_enabled,
        "project_articles": project_article_summary(db, settings),
        "auto_publish": settings.knowledge_curation_auto_publish,
        "content_language": settings.knowledge_content_language,
        "provider": settings.generation_provider,
        "model": settings.generation_model,
        "configured_model_digest": settings.generation_model_digest,
        "prompt_version": settings.generation_prompt_version,
        "fallback_models": [
            item.strip()
            for item in settings.generation_fallback_models.split(",")
            if item.strip() and item.strip() != settings.generation_model
        ],
        "generation_parameters": {
            "temperature": settings.generation_temperature,
            "context_window": settings.generation_context_window,
            "keep_alive": settings.generation_keep_alive,
        },
        "gpu_policy": {
            "minimum_free_mb": settings.knowledge_curation_gpu_min_free_mb,
            "maximum_utilization_percent": (settings.knowledge_curation_gpu_max_utilization),
            "maximum_temperature_c": settings.knowledge_curation_gpu_max_temperature,
            "maximum_checks_per_cycle": settings.knowledge_curation_busy_max_checks,
            "exhausted_cooldown_seconds": (settings.knowledge_curation_exhausted_cooldown_seconds),
        },
        "scheduler": state,
        "editor_connection": editor_connection,
        "qualification": qualification,
        "eligible_candidates": eligible_candidates,
        "record_summary": record_summary,
    }


@app.get("/api/v1/knowledge/dedup/status")
def knowledge_dedup_status(db: Session = Depends(get_db)) -> dict:
    pending = int(
        db.scalar(
            select(func.count())
            .select_from(KnowledgeCandidate)
            .where(
                KnowledgeCandidate.evidence_gate_status == "VERIFIED",
                KnowledgeCandidate.metadata_json["semantic_dedup"]["state"].astext
                == "pending_gpu_vector_check"
            )
        )
        or 0
    )
    blocked = int(
        db.scalar(
            select(func.count())
            .select_from(KnowledgeCandidate)
            .where(
                KnowledgeCandidate.evidence_gate_status != "VERIFIED",
                KnowledgeCandidate.metadata_json["semantic_dedup"]["state"].astext.in_(
                    ["pending_gpu_vector_check", "blocked_by_evidence_gate"]
                ),
            )
        )
        or 0
    )
    vectors = int(
        db.scalar(
            select(func.count())
            .select_from(KnowledgeSimilarityEmbedding)
            .where(KnowledgeSimilarityEmbedding.embedding_revision == settings.embedding_revision)
        )
        or 0
    )
    return {
        "policy": "key_terms_then_pgvector-v1",
        "embedding_revision": settings.embedding_revision,
        "pending_candidates": pending,
        "blocked_by_evidence_gate": blocked,
        "rebuildable_vectors": vectors,
        "runtime_mode": settings.embedding_runtime_mode,
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
def publish(
    candidate_id: uuid.UUID,
    request: CandidatePublish,
    db: Session = Depends(get_db),
) -> dict:
    if settings.knowledge_curation_enabled:
        raise HTTPException(
            409,
            "manual publication is disabled while the local evidence editor is enabled",
        )
    row = _candidate_or_404(db, candidate_id)
    row.metadata_json = {
        **(row.metadata_json or {}),
        "approval_policy": "human_review",
        "approved_by": request.reviewer,
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "approval_confirmation": request.confirmation,
    }
    case, outcome = publish_candidate(db, row)
    materialized_path = None
    if case is not None:
        materialized_path = materialize_case(
            db,
            case,
            vault_dir=settings.vault_dir,
            pipeline_version=settings.pipeline_version,
            content_language=settings.knowledge_content_language,
        )
    db.commit()
    return {
        "candidate_id": str(row.id),
        "case_id": str(case.id) if case else None,
        "outcome": outcome,
        "status": row.status,
        "materialized_path": str(materialized_path) if materialized_path else None,
    }


def _case_json(row: KnowledgeCase) -> dict:
    metadata = _display_metadata(row.metadata_json)
    return {
        "id": str(row.id),
        "category": row.category,
        "title": redact_text(row.title),
        "problem": redact_text(row.problem),
        "symptom": redact_text(row.symptom),
        "root_cause": redact_text(row.root_cause),
        "solution": redact_text(row.solution),
        "status": row.status,
        "occurrence_count": row.occurrence_count,
        "current_revision_id": str(row.current_revision_id) if row.current_revision_id else None,
        "first_seen_at": row.first_seen_at,
        "last_seen_at": row.last_seen_at,
        "project": metadata.get("project") or "unassigned",
        "tags": metadata.get("tags") or [],
        "metadata": metadata,
    }


@app.get("/api/v1/knowledge/cases")
def knowledge_cases(
    category: str | None = None,
    project: str | None = None,
    tags: list[str] = Query(default=[]),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> dict:
    statement = select(KnowledgeCase).where(KnowledgeCase.status == "verified")
    count_statement = (
        select(func.count()).select_from(KnowledgeCase).where(KnowledgeCase.status == "verified")
    )
    if category:
        statement = statement.where(KnowledgeCase.category == category)
        count_statement = count_statement.where(KnowledgeCase.category == category)
    if project:
        statement = statement.where(KnowledgeCase.metadata_json["project"].as_string() == project)
        count_statement = count_statement.where(
            KnowledgeCase.metadata_json["project"].as_string() == project
        )
    for tag in sorted({_normalize_knowledge_tag(item) for item in tags if item.strip()}):
        predicate = KnowledgeCase.metadata_json.contains({"tags": [tag]})
        statement = statement.where(predicate)
        count_statement = count_statement.where(predicate)
    rows = db.scalars(
        statement.order_by(KnowledgeCase.last_seen_at.desc(), KnowledgeCase.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "items": [_case_json(row) for row in rows],
        "page": page,
        "total": db.scalar(count_statement),
    }


@app.get("/api/v1/knowledge/facets")
def knowledge_facets(
    kind: str = Query("cases", pattern="^(cases|candidates|journal)$"),
    db: Session = Depends(get_db),
) -> dict:
    if kind == "cases":
        category_rows = db.execute(
            text(
                """
            SELECT
              COALESCE(metadata->>'project', 'unassigned') AS project,
              category,
              count(*)::int AS count,
              max(last_seen_at) AS latest_at
            FROM knowledge_case
            WHERE status = 'verified'
            GROUP BY COALESCE(metadata->>'project', 'unassigned'), category
            ORDER BY max(last_seen_at) DESC, project, category
            """
            )
        ).mappings()
    elif kind == "candidates":
        category_rows = db.execute(
            text(
                """
            SELECT
              COALESCE(metadata->>'project', 'unassigned') AS project,
              category,
              count(*)::int AS count,
              max(updated_at) AS latest_at
            FROM knowledge_candidate
            WHERE status NOT IN ('published', 'activity_only')
            GROUP BY COALESCE(metadata->>'project', 'unassigned'), category
            ORDER BY max(updated_at) DESC, project, category
            """
            )
        ).mappings()
    else:
        category_rows = db.execute(
            text(
                """
            SELECT project_key AS project,
              COALESCE(
                metadata->>'work_type',
                CASE
                  WHEN jsonb_array_length(failures) > 0 THEN 'error_resolution'
                  WHEN lower(title || ' ' || change_summary)
                    ~ 'cpu|latency|performance|load|성능|부하|지연'
                    THEN 'performance'
                  WHEN significance_reasons
                    @> ARRAY['operational_or_configuration_change']::text[]
                    THEN 'operations'
                  ELSE 'implementation'
                END
              ) AS category,
              count(*)::int AS count,
              max(occurred_at) AS latest_at
            FROM project_journal_entry
            GROUP BY project_key, category
            ORDER BY max(occurred_at) DESC, project, category
            """
            )
        ).mappings()
    projects: dict[str, dict] = {}
    for row in category_rows:
        project = str(row["project"])
        group = projects.setdefault(
            project,
            {
                "key": project,
                "count": 0,
                "latest_at": row["latest_at"],
                "categories": [],
            },
        )
        group["count"] += int(row["count"])
        group["categories"].append(
            {
                "key": row["category"],
                "count": int(row["count"]),
                "latest_at": row["latest_at"],
            }
        )
    tag_rows = []
    if kind == "cases":
        tag_rows = db.execute(
            text(
                """
            SELECT tag, count(*)::int AS count, max(last_seen_at) AS latest_at
            FROM knowledge_case
            CROSS JOIN LATERAL jsonb_array_elements_text(
              COALESCE(metadata->'tags', '[]'::jsonb)
            ) AS tags(tag)
            WHERE status = 'verified'
            GROUP BY tag
            ORDER BY max(last_seen_at) DESC, tag
            LIMIT 500
            """
            )
        ).mappings()
    return {
        "projects": list(projects.values()),
        "tags": [
            {
                "key": row["tag"],
                "count": int(row["count"]),
                "latest_at": row["latest_at"],
            }
            for row in tag_rows
        ],
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
                "content": redact_value(item.content_json),
                "evidence_summary": redact_value(item.evidence_summary),
                "created_at": item.created_at,
            }
            for item in revisions
        ],
        "occurrences": [
            {
                "id": str(item.id),
                "candidate_id": str(item.candidate_id),
                "activity_id": str(item.activity_id) if item.activity_id else None,
                "evidence": redact_value(item.evidence_json),
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
