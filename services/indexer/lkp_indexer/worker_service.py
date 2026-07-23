from __future__ import annotations

import argparse
import signal
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone

from lkp.db import SessionLocal
from lkp.models import IngestJob, JobStatus, WorkerHeartbeat
from lkp.settings import get_settings
from sqlalchemy import update

from .cli import get_embedder
from .queue import lease
from .service_runtime import service_pid
from .worker import heartbeat, process_job


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
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    stopping = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    embedder = get_embedder(settings, deterministic)
    while not stopping.is_set():
        try:
            with SessionLocal() as session:
                heartbeat(session, worker_id, "idle")
                job = lease(session, worker_id, settings.lease_seconds)
                session.commit()
        except Exception:
            stopping.wait(2)
            continue
        if job is None:
            if once:
                break
            stopping.wait(1)
            continue
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
    try:
        with SessionLocal() as session:
            heartbeat(session, worker_id, "stopped")
            session.commit()
    except Exception:
        pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deterministic-test-embedding", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    with service_pid():
        return run(args.deterministic_test_embedding, args.once)


if __name__ == "__main__":
    raise SystemExit(main())
