import asyncio
import hashlib
from pathlib import Path

from lkp.models import Document, IngestEvent, SourceRoot
from lkp.settings import Settings
from sqlalchemy import select
from sqlalchemy.orm import Session
from watchfiles import Change, awatch

from .chunking import SUPPORTED_EXTENSIONS
from .file_safety import source_file_rejection_reason
from .ignore import IgnoreRules
from .paths import idempotency_key, is_reparse_point
from .queue import enqueue
from .reconcile import reconcile_root


async def _stable_file(path: Path, wait_seconds: float) -> bool:
    previous: tuple[int, int] | None = None
    for _ in range(6):
        try:
            stat = path.stat()
        except OSError:
            return False
        current = (stat.st_size, stat.st_mtime_ns)
        if previous == current:
            return True
        previous = current
        await asyncio.sleep(max(0.05, wait_seconds))
    return False


def _content_hash(path: Path, max_bytes: int) -> str | None:
    try:
        if path.stat().st_size > max_bytes:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


async def watch_root(
    session_factory,
    source_root: SourceRoot,
    *,
    debounce_ms: int = 750,
    force_polling: bool = False,
    poll_delay_ms: int = 2000,
    stability_seconds: float = 0.5,
    max_file_bytes: int = 10 * 1024 * 1024,
    stop_event: asyncio.Event | None = None,
) -> None:
    root = Path(source_root.canonical_path)
    rules = IgnoreRules(root, source_root.exclude_patterns)
    async for changes in awatch(
        root,
        debounce=debounce_ms,
        force_polling=force_polling,
        poll_delay_ms=poll_delay_ms,
        stop_event=stop_event,
        recursive=True,
    ):
        merged: dict[str, Change] = {}
        for change, raw_path in changes:
            candidate = Path(raw_path).resolve(strict=False)
            try:
                relative = candidate.relative_to(root).as_posix()
            except ValueError:
                continue
            if rules.matches(relative, is_dir=candidate.is_dir()):
                continue
            if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if candidate.exists() and is_reparse_point(candidate):
                continue
            merged[str(candidate)] = change
        if not merged:
            continue
        added = {
            canonical
            for canonical, change in merged.items()
            if change in {Change.added, Change.modified} and Path(canonical).is_file()
        }
        for canonical in list(added):
            if not await _stable_file(Path(canonical), stability_seconds):
                added.remove(canonical)
        with session_factory() as session:
            session: Session
            unsupported: dict[str, str] = {}
            deleted = [
                canonical
                for canonical, change in merged.items()
                if change == Change.deleted or not Path(canonical).exists()
            ]
            deleted_by_hash = {
                row.current_content_hash: row.canonical_path
                for row in session.scalars(
                    select(Document).where(
                        Document.source_root_id == source_root.id,
                        Document.canonical_path.in_(deleted),
                    )
                )
                if row.current_content_hash
            }
            renamed_from: set[str] = set()
            rename_for: dict[str, str] = {}
            for canonical in added:
                digest = _content_hash(Path(canonical), max_file_bytes)
                if digest and digest in deleted_by_hash:
                    old_path = deleted_by_hash[digest]
                    renamed_from.add(old_path)
                    rename_for[canonical] = old_path
            for canonical, _change in merged.items():
                path = Path(canonical)
                if path.exists() and path.is_file():
                    try:
                        reason = source_file_rejection_reason(path, max_file_bytes)
                    except OSError:
                        reason = "unreadable"
                    if reason:
                        unsupported[canonical] = reason
                        continue
                    info = path.stat()
                    old_path = rename_for.get(canonical)
                    job_type = "watch_rename" if old_path else "watch_index"
                    details = {"rename_from": old_path} if old_path else {}
                    key_path = f"rename:{old_path}->{canonical}" if old_path else canonical
                    key = idempotency_key(
                        str(source_root.id), key_path, info.st_size, info.st_mtime_ns
                    )
                    priority = 10 if old_path else 20
                else:
                    if canonical in renamed_from:
                        continue
                    key = idempotency_key(str(source_root.id), canonical, 0, 0)
                    job_type = "watch_delete"
                    details = {}
                    priority = 20
                enqueue(
                    session,
                    key=key,
                    source_root_id=source_root.id,
                    canonical_path=canonical,
                    job_type=job_type,
                    details=details,
                    priority=priority,
                )
            session.add(
                IngestEvent(
                    source_root_id=source_root.id,
                    event_type="watcher_batch",
                    path=source_root.canonical_path,
                    details={
                        "event_count": len(changes),
                        "merged_count": len(merged),
                        "renames": len(rename_for),
                        "unsupported_count": len(unsupported),
                        "unsupported_reasons": sorted(set(unsupported.values())),
                    },
                )
            )
            session.commit()


async def reconciliation_loop(
    session_factory,
    source_root: SourceRoot,
    settings: Settings,
    stop_event: asyncio.Event,
    interval_seconds: int | None = None,
) -> None:
    interval = max(5, interval_seconds or settings.reconciliation_seconds)
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            with session_factory() as session:
                current_root = session.get(SourceRoot, source_root.id)
                if current_root is not None and current_root.enabled:
                    reconcile_root(session, current_root, settings)
                    session.commit()
