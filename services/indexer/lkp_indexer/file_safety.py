from __future__ import annotations

from pathlib import Path


def source_file_rejection_reason(path: Path, max_file_bytes: int) -> str | None:
    """Return a stable reason for inputs that must not enter the ingest queue."""

    info = path.stat()
    if info.st_size > max_file_bytes:
        return "oversized"
    with path.open("rb") as handle:
        sample = handle.read(8192)
    if b"\x00" in sample:
        return "binary"
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return "invalid_utf8"
    return None
