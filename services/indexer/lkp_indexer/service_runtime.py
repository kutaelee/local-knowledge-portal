from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


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
