from __future__ import annotations

import argparse
import signal
import socket
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import structlog
from lkp.db import SessionLocal
from lkp.logging import configure_logging
from lkp.models import IngestJob, JobStatus, SourceRoot, WorkerHeartbeat
from lkp.settings import get_settings
from sqlalchemy import update

from .cli import get_embedder
from .queue import lease
from .selection import semantic_policy
from .service_runtime import assert_mount_guards, service_pid
from .worker import heartbeat, process_job

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class WorkloadPolicy:
    cooldown_seconds: float
    burst_jobs: int
    burst_cooldown_seconds: float


def workload_policy(resource_class: str, settings) -> WorkloadPolicy:
    if resource_class == "lexical":
        return WorkloadPolicy(
            cooldown_seconds=settings.worker_lexical_job_cooldown_seconds,
            burst_jobs=settings.worker_lexical_burst_jobs,
            burst_cooldown_seconds=settings.worker_lexical_burst_cooldown_seconds,
        )
    return WorkloadPolicy(
        cooldown_seconds=settings.worker_job_cooldown_seconds,
        burst_jobs=settings.worker_burst_jobs,
        burst_cooldown_seconds=settings.worker_burst_cooldown_seconds,
    )


def _resource_policy(settings) -> dict:
    return {
        "embedding_batch_size": settings.embedding_batch_size,
        "embedding_batch_cooldown_seconds": settings.embedding_batch_cooldown_seconds,
        "job_cooldown_seconds": settings.worker_job_cooldown_seconds,
        "burst_jobs": settings.worker_burst_jobs,
        "burst_cooldown_seconds": settings.worker_burst_cooldown_seconds,
        "lexical_job_cooldown_seconds": (
            settings.worker_lexical_job_cooldown_seconds
        ),
        "lexical_burst_jobs": settings.worker_lexical_burst_jobs,
        "lexical_burst_cooldown_seconds": (
            settings.worker_lexical_burst_cooldown_seconds
        ),
        "pause_file": str(settings.worker_pause_file),
        "resource_guard_enabled": True,
    }


def _renew_lease(
    stop: threading.Event,
    worker_id: str,
    job_id: uuid.UUID,
    lease_seconds: int,
    heartbeat_seconds: int,
) -> None:
    interval = max(1, min(heartbeat_seconds, lease_seconds // 3))
    while not stop.wait(interval):
        with SessionLocal() as session:
            session.execute(
                update(IngestJob)
                .where(
                    IngestJob.id == job_id,
                    IngestJob.leased_by == worker_id,
                    IngestJob.status.in_([JobStatus.leased, JobStatus.processing]),
                )
                .values(
                    lease_expires_at=datetime.now(timezone.utc)
                    + timedelta(seconds=lease_seconds),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            row = session.get(WorkerHeartbeat, worker_id)
            if row:
                row.last_seen_at = datetime.now(timezone.utc)
            session.commit()


def run(deterministic: bool = False, once: bool = False) -> int:
    settings = get_settings()
    assert_mount_guards(settings)
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    stopping = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    embedder = get_embedder(settings, deterministic)
    policy = _resource_policy(settings)
    jobs_in_burst = {"semantic": 0, "lexical": 0}
    pause_logged = False
    logger.info("worker_resource_policy", **policy)
    while not stopping.is_set():
        if settings.worker_pause_file.exists():
            try:
                with SessionLocal() as session:
                    heartbeat(
                        session,
                        worker_id,
                        "paused",
                        metadata={
                            **policy,
                            "pause_requested": True,
                            "pause_reason": "operator_pause_file",
                        },
                    )
                    session.commit()
            except Exception:
                pass
            if not pause_logged:
                logger.warning(
                    "worker_paused",
                    pause_file=str(settings.worker_pause_file),
                    reason="operator_pause_file",
                )
                pause_logged = True
            stopping.wait(settings.worker_pause_poll_seconds)
            continue
        if pause_logged:
            logger.info("worker_resumed", pause_file=str(settings.worker_pause_file))
            pause_logged = False
        try:
            with SessionLocal() as session:
                heartbeat(
                    session,
                    worker_id,
                    "idle",
                    metadata={**policy, "pause_requested": False},
                )
                job = lease(session, worker_id, settings.lease_seconds)
                resource_class = "semantic"
                if job is not None:
                    root = session.get(SourceRoot, job.source_root_id)
                    if root is not None:
                        semantic_allowed, _ = semantic_policy(
                            Path(job.canonical_path),
                            root,
                            repository_mode=settings.repository_embedding_mode,
                        )
                        resource_class = (
                            "semantic" if semantic_allowed else "lexical"
                        )
                session.commit()
        except Exception:
            stopping.wait(2)
            continue
        if job is None:
            if once:
                break
            stopping.wait(1)
            continue
        with SessionLocal() as session:
            heartbeat(
                session,
                worker_id,
                "busy",
                job.id,
                metadata={**policy, "pause_requested": False},
            )
            session.commit()
        renew_stop = threading.Event()
        renewer = threading.Thread(
            target=_renew_lease,
            args=(
                renew_stop,
                worker_id,
                job.id,
                settings.lease_seconds,
                settings.heartbeat_seconds,
            ),
            daemon=True,
        )
        renewer.start()
        try:
            try:
                with SessionLocal() as session:
                    current = session.get(IngestJob, job.id)
                    if current:
                        current.status = JobStatus.processing
                        session.commit()
                with SessionLocal() as session:
                    current = session.get(IngestJob, job.id)
                    if current:
                        try:
                            process_job(session, current, settings, embedder, worker_id)
                        except Exception:
                            session.commit()
                        else:
                            session.commit()
            except Exception:
                stopping.wait(2)
        finally:
            renew_stop.set()
            renewer.join(timeout=2)
        if once:
            break
        selected_policy = workload_policy(resource_class, settings)
        jobs_in_burst[resource_class] += 1
        if jobs_in_burst[resource_class] >= selected_policy.burst_jobs:
            try:
                with SessionLocal() as session:
                    heartbeat(
                        session,
                        worker_id,
                        "cooldown",
                        metadata={
                            **policy,
                            "pause_requested": False,
                            "cooldown_reason": "burst_limit",
                            "resource_class": resource_class,
                        },
                    )
                    session.commit()
            except Exception:
                pass
            logger.info(
                "worker_burst_cooldown",
                completed_jobs=jobs_in_burst[resource_class],
                cooldown_seconds=selected_policy.burst_cooldown_seconds,
                resource_class=resource_class,
            )
            stopping.wait(selected_policy.burst_cooldown_seconds)
            jobs_in_burst[resource_class] = 0
        else:
            stopping.wait(selected_policy.cooldown_seconds)
    try:
        with SessionLocal() as session:
            heartbeat(session, worker_id, "stopped")
            session.commit()
    except Exception:
        pass
    return 0


def main() -> int:
    configure_logging("worker")
    parser = argparse.ArgumentParser()
    parser.add_argument("--deterministic-test-embedding", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    with service_pid():
        return run(args.deterministic_test_embedding, args.once)


if __name__ == "__main__":
    raise SystemExit(main())
