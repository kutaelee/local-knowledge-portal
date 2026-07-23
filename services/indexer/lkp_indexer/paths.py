import hashlib
import os
import stat
from pathlib import Path


class UnsafePathError(ValueError):
    pass


def canonicalize(path: Path, root: Path, *, must_exist: bool = True) -> Path:
    resolved_root = root.resolve(strict=True)
    resolved = path.resolve(strict=must_exist)
    try:
        if os.path.commonpath([str(resolved_root), str(resolved)]) != str(resolved_root):
            raise UnsafePathError(f"path escapes source root: {path}")
    except ValueError as exc:
        raise UnsafePathError(f"path is on a different volume: {path}") from exc
    return resolved


def is_reparse_point(path: Path) -> bool:
    info = path.lstat()
    attrs = getattr(info, "st_file_attributes", 0)
    return path.is_symlink() or bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def content_hash(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def idempotency_key(root_id: str, canonical_path: str, size: int, mtime_ns: int) -> str:
    material = f"{root_id}\0{canonical_path.casefold()}\0{size}\0{mtime_ns}".encode()
    return hashlib.sha256(material).hexdigest()
