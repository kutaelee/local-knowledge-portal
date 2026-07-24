from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


def assert_mount_guards(settings) -> None:
    missing = [
        str(path)
        for path in settings.mount_guard_path_list
        if not path.is_file()
    ]
    empty = []
    for path in settings.mount_guard_nonempty_dir_list:
        try:
            if not path.is_dir() or next(path.iterdir(), None) is None:
                empty.append(str(path))
        except OSError:
            empty.append(str(path))
    if missing or empty:
        raise RuntimeError(
            f"mount guard failed; missing={missing}; empty_or_unreadable={empty}"
        )


@contextmanager
def service_pid():
    raw_path = os.getenv("LKP_SERVICE_PID_FILE")
    path = Path(raw_path) if raw_path else None
    current = str(os.getpid())
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{current}.tmp")
        temporary.write_text(current, encoding="ascii")
        os.replace(temporary, path)
    try:
        yield
    finally:
        if path:
            try:
                if path.read_text(encoding="ascii").strip() == current:
                    path.unlink()
            except OSError:
                pass
