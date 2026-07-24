from __future__ import annotations

import argparse
import asyncio
import os
import signal
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog
from lkp.db import SessionLocal
from lkp.logging import configure_logging
from lkp.models import WorkerHeartbeat
from lkp.settings import get_settings

from .reconcile import reconcile_root
from .scanner import load_roots, register_roots
from .service_runtime import assert_mount_guards, service_pid
from .watcher import reconciliation_loop, watch_root

logger = structlog.get_logger()


@dataclass(frozen=True, slots=True)
class CpuSample:
    percent: float
    consecutive_high: int
    alert: bool
    in_grace: bool


class ProcessCpuMonitor:
    def __init__(
        self,
        warning_percent: float,
        warning_samples: int,
        grace_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        process_time: Callable[[], float] = time.process_time,
    ) -> None:
        self.warning_percent = warning_percent
        self.warning_samples = warning_samples
        self.grace_seconds = grace_seconds
        self._monotonic = monotonic
        self._process_time = process_time
        self._started_wall = monotonic()
        self._last_wall = self._started_wall
        self._last_cpu = process_time()
        self._consecutive_high = 0

    def sample(self) -> CpuSample:
        wall = self._monotonic()
        cpu = self._process_time()
        elapsed_wall = max(wall - self._last_wall, 1e-9)
        percent = max(0.0, (cpu - self._last_cpu) / elapsed_wall * 100)
        self._last_wall = wall
        self._last_cpu = cpu
        in_grace = wall - self._started_wall < self.grace_seconds
        if not in_grace and percent >= self.warning_percent:
            self._consecutive_high += 1
        else:
            self._consecutive_high = 0
        return CpuSample(
            percent=percent,
            consecutive_high=self._consecutive_high,
            alert=self._consecutive_high >= self.warning_samples,
            in_grace=in_grace,
        )


def select_watch_mode(
    canonical_path: str,
    *,
    force_polling: bool,
    polling_roots: set[str],
) -> str:
    normalized = str(os.path.realpath(canonical_path))
    return "polling" if force_polling or normalized in polling_roots else "native"


def _health(
    worker_id: str,
    state: str,
    error: Exception | None = None,
    metadata: dict | None = None,
) -> None:
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
            row.metadata_json = {**(row.metadata_json or {}), **(metadata or {})}
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


async def _monitor_service(
    worker_id: str,
    stopping: asyncio.Event,
    *,
    heartbeat_seconds: float,
    warning_percent: float,
    warning_samples: int,
    grace_seconds: float,
    root_watch_modes: dict[str, str],
    root_reconcile_intervals: dict[str, int],
    watch_poll_delay_ms: int,
    reconciliation_seconds: int,
    source_root_count: int,
) -> None:
    monitor = ProcessCpuMonitor(
        warning_percent,
        warning_samples,
        grace_seconds,
    )
    distinct_modes = set(root_watch_modes.values())
    mode = distinct_modes.pop() if len(distinct_modes) == 1 else "hybrid"
    logger.info(
        "watcher_mode_selected",
        watch_mode=mode,
        root_watch_modes=root_watch_modes,
        root_reconcile_intervals=root_reconcile_intervals,
        poll_delay_ms=watch_poll_delay_ms,
        reconciliation_seconds=reconciliation_seconds,
        source_root_count=source_root_count,
    )
    previous_alert = False
    while not stopping.is_set():
        sample = monitor.sample()
        metadata = {
            "watch_mode": mode,
            "root_watch_modes": root_watch_modes,
            "root_reconcile_intervals": root_reconcile_intervals,
            "poll_delay_ms": watch_poll_delay_ms,
            "reconciliation_seconds": reconciliation_seconds,
            "source_root_count": source_root_count,
            "process_cpu_percent": round(sample.percent, 2),
            "cpu_warning_percent": warning_percent,
            "cpu_warning_samples": warning_samples,
            "cpu_consecutive_high": sample.consecutive_high,
            "cpu_alert": sample.alert,
            "cpu_grace_active": sample.in_grace,
        }
        _health(worker_id, "error" if sample.alert else "healthy", metadata=metadata)
        if sample.alert != previous_alert:
            log = logger.warning if sample.alert else logger.info
            log(
                "watcher_cpu_alert_changed",
                alert=sample.alert,
                process_cpu_percent=round(sample.percent, 2),
                consecutive_high=sample.consecutive_high,
                threshold_percent=warning_percent,
            )
            previous_alert = sample.alert
        try:
            await asyncio.wait_for(stopping.wait(), timeout=heartbeat_seconds)
        except TimeoutError:
            continue
    _health(worker_id, "stopped", metadata={"cpu_alert": False})


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
    assert_mount_guards(settings)
    configured_roots = load_roots(settings.source_roots_config)
    runtime_policies = {
        str(os.path.realpath(item["path"])): item for item in configured_roots
    }
    with SessionLocal() as session:
        roots = register_roots(session, settings.source_roots_config)
        for root in roots:
            policy = runtime_policies.get(root.canonical_path, {})
            if policy.get("startup_reconcile", True):
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
    root_watch_modes: dict[str, str] = {}
    root_reconcile_intervals: dict[str, int] = {}
    for root in roots:
        policy = runtime_policies.get(root.canonical_path, {})
        configured_mode = str(policy.get("watch_mode", "auto")).casefold()
        if configured_mode not in {"auto", "native", "polling", "disabled"}:
            raise ValueError(f"invalid watch_mode for {root.canonical_path}: {configured_mode}")
        root_watch_modes[root.canonical_path] = (
            select_watch_mode(
                root.canonical_path,
                force_polling=settings.watch_force_polling,
                polling_roots=settings.watch_polling_root_set,
            )
            if configured_mode == "auto"
            else configured_mode
        )
        root_reconcile_intervals[root.canonical_path] = max(
            60,
            int(policy.get("reconcile_interval_seconds", settings.reconciliation_seconds)),
        )
    tasks = []
    tasks.append(
        asyncio.create_task(
            _monitor_service(
                "watcher-service",
                stopping,
                heartbeat_seconds=settings.heartbeat_seconds,
                warning_percent=settings.watch_cpu_warning_percent,
                warning_samples=settings.watch_cpu_warning_samples,
                grace_seconds=settings.watch_cpu_grace_seconds,
                root_watch_modes=root_watch_modes,
                root_reconcile_intervals=root_reconcile_intervals,
                watch_poll_delay_ms=settings.watch_poll_delay_ms,
                reconciliation_seconds=settings.reconciliation_seconds,
                source_root_count=len(roots),
            )
        )
    )
    for root in roots:
        watch_id = f"watcher:{root.id}"
        reconcile_id = f"reconciler:{root.id}"
        if root_watch_modes[root.canonical_path] != "disabled":
            tasks.append(
                asyncio.create_task(
                    _supervise(
                        watch_id,
                        lambda root=root: watch_root(
                            SessionLocal,
                            root,
                            debounce_ms=settings.watch_debounce_ms,
                            force_polling=root_watch_modes[root.canonical_path] == "polling",
                            poll_delay_ms=settings.watch_poll_delay_ms,
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
                        SessionLocal,
                        root,
                        settings,
                        stopping,
                        root_reconcile_intervals[root.canonical_path],
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
    configure_logging("watcher")
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    with service_pid():
        return asyncio.run(run(args.once))


if __name__ == "__main__":
    raise SystemExit(main())
