import asyncio
from pathlib import Path

from lkp.models import SourceRoot
from sqlalchemy.orm import Session
from watchfiles import Change, awatch

from .paths import idempotency_key
from .queue import enqueue


async def watch_root(
    session_factory,
    source_root: SourceRoot,
    *,
    debounce_ms: int = 750,
    stop_event: asyncio.Event | None = None,
) -> None:
    root = Path(source_root.canonical_path)
    async for changes in awatch(
        root,
        debounce=debounce_ms,
        stop_event=stop_event,
        recursive=True,
    ):
        merged: dict[str, Change] = {}
        for change, raw_path in changes:
            merged[str(Path(raw_path).resolve(strict=False))] = change
        with session_factory() as session:
            session: Session
            for canonical, _change in merged.items():
                path = Path(canonical)
                if path.exists() and path.is_file():
                    info = path.stat()
                    key = idempotency_key(
                        str(source_root.id), canonical, info.st_size, info.st_mtime_ns
                    )
                    job_type = "watch_index"
                else:
                    key = idempotency_key(str(source_root.id), canonical, 0, 0)
                    job_type = "watch_delete"
                enqueue(
                    session,
                    key=key,
                    source_root_id=source_root.id,
                    canonical_path=canonical,
                    job_type=job_type,
                )
            session.commit()
