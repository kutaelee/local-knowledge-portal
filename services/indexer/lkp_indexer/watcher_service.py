from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from lkp.db import SessionLocal
from lkp.models import WorkerHeartbeat
from lkp.settings import get_settings

from .reconcile import reconcile_root
from .scanner import register_roots
from .service_runtime import service_pid
from .watcher import reconciliation_loop, watch_root


def _health(worker_id: str, state: str, error: Exception | None = None) -> None:
    try:
        with SessionLocal() as session:
            row = session.get(WorkerHeartbeat, worker_id)
            if row is None:
                row = WorkerHeartbeat(
                    worker_id=worker_id,
                    hostname=socket.gethostname(),
                    process_id=os.getpid(),
                    version="0.1.0",
                    state=state,
                    processed_count=0,
                    failed_count=0,
                )
                session.add(row)
            row.process_id = os.getpid()
            row.last_seen_at = datetime.now(timezone.utc)
            row.state = state
            if error:
                row.failed_count += 1
                row.metadata_json = {
                    **row.metadata_json,
                    "last_error_type": type(error).__name__,
                    "last_error": str(error)[:1000],
                }
            session.commit()
    except Exception:
        pass


async def _supervise(
    worker_id: str,
    operation: Callable[[], Awaitable[None]],
    stopping: asyncio.Event,
    heartbeat_seconds: float,
) -> None:
    async def heartbeat() -> None:
        while not stopping.is_set():
            _health(worker_id, "healthy")
            try:
                await asyncio.wait_for(stopping.wait(), timeout=heartbeat_seconds)
            except TimeoutError:
                continue

    heartbeat_task = asyncio.create_task(heartbeat())
    while not stopping.is_set():
        try:
            await operation()
            break
        except Exception as exc:
            _health(worker_id, "error", exc)
            try:
                await asyncio.wait_for(stopping.wait(), timeout=2)
            except TimeoutError:
                continue
    heartbeat_task.cancel()
    await asyncio.gather(heartbeat_task, return_exceptions=True)
    _health(worker_id, "stopped")


async def run(once: bool = False) -> int:
    settings = get_settings()
    with SessionLocal() as session:
        roots = register_roots(session, settings.source_roots_config)
        for root in roots:
            reconcile_root(session, root, settings)
        session.commit()
        for root in roots:
            session.expunge(root)
    if once:
        return 0
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stopping.set))
    tasks = []
    for root in roots:
        watch_id = f"watcher:{root.id}"
        reconcile_id = f"reconciler:{root.id}"
        tasks.append(
            asyncio.create_task(
                _supervise(
                    watch_id,
                    lambda root=root: watch_root(
                        SessionLocal,
                        root,
                        stability_seconds=settings.file_stability_seconds,
                        max_file_bytes=settings.max_file_bytes,
                        stop_event=stopping,
                    ),
                    stopping,
                    settings.heartbeat_seconds,
                )
            )
        )
        tasks.append(
            asyncio.create_task(
                _supervise(
                    reconcile_id,
                    lambda root=root: reconciliation_loop(
                        SessionLocal, root, settings, stopping
                    ),
                    stopping,
                    settings.heartbeat_seconds,
                )
            )
        )
    await stopping.wait()
    await asyncio.gather(*tasks, return_exceptions=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    with service_pid():
        return asyncio.run(run(args.once))


if __name__ == "__main__":
    raise SystemExit(main())
