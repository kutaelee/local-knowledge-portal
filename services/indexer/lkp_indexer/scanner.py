import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from lkp.models import SourceRoot
from sqlalchemy import select
from sqlalchemy.orm import Session

from .chunking import SUPPORTED_EXTENSIONS
from .file_safety import source_file_rejection_reason
from .ignore import IgnoreRules
from .paths import canonicalize, idempotency_key, is_reparse_point
from .queue import enqueue


@dataclass(slots=True)
class ScanStats:
    visited: int = 0
    queued: int = 0
    ignored: int = 0
    unsupported: int = 0
    errors: int = 0


def load_roots(path: Path) -> list[dict]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return payload.get("source_roots", [])


def register_roots(session: Session, config_path: Path) -> list[SourceRoot]:
    roots = []
    for item in load_roots(config_path):
        canonical = str(Path(item["path"]).resolve(strict=True))
        enabled = item.get("enabled", True)
        root = session.scalar(select(SourceRoot).where(SourceRoot.canonical_path == canonical))
        if root is None:
            root = SourceRoot(
                name=item.get("name", item["id"]),
                canonical_path=canonical,
                source_type=item["type"],
                data_scope=item.get("data_scope", "production"),
                read_only=item.get("read_only", True),
                enabled=enabled,
                include_patterns=item.get("include_patterns", ["**/*"]),
                exclude_patterns=item.get("exclude_patterns", []),
            )
            session.add(root)
            session.flush()
        else:
            root.name = item.get("name", item["id"])
            root.source_type = item["type"]
            root.data_scope = item.get("data_scope", "production")
            root.read_only = item.get("read_only", True)
            root.enabled = enabled
            root.include_patterns = item.get("include_patterns", ["**/*"])
            root.exclude_patterns = item.get("exclude_patterns", [])
        if root.enabled:
            roots.append(root)
    return roots


def scan_bases(root: Path, source_type: str) -> list[Path]:
    if source_type != "repository_collection":
        return [root]
    return sorted(
        (
            child
            for child in root.iterdir()
            if child.is_dir()
            and not is_reparse_point(child)
            and ((child / ".git").is_dir() or (child / ".git").is_file())
        ),
        key=lambda item: item.name.casefold(),
    )


def scan_root(session: Session, source_root: SourceRoot, max_file_bytes: int) -> ScanStats:
    root = Path(source_root.canonical_path)
    rules = IgnoreRules(root, source_root.exclude_patterns)
    stats = ScanStats()
    try:
        bases = scan_bases(root, source_root.source_type)
    except OSError:
        stats.errors += 1
        return stats
    for current, directories, files in (
        item
        for scan_base in bases
        for item in os.walk(scan_base, topdown=True, followlinks=False)
    ):
        current_path = Path(current)
        safe_directories = []
        for name in directories:
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if rules.matches(relative, is_dir=True) or is_reparse_point(candidate):
                stats.ignored += 1
            else:
                safe_directories.append(name)
        directories[:] = safe_directories
        for name in files:
            stats.visited += 1
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            try:
                if rules.matches(relative) or is_reparse_point(candidate):
                    stats.ignored += 1
                    continue
                if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    stats.unsupported += 1
                    continue
                info = candidate.stat()
                if source_file_rejection_reason(candidate, max_file_bytes):
                    stats.unsupported += 1
                    continue
                canonical = canonicalize(candidate, root)
                key = idempotency_key(
                    str(source_root.id), str(canonical), info.st_size, info.st_mtime_ns
                )
                if enqueue(
                    session,
                    key=key,
                    source_root_id=source_root.id,
                    canonical_path=str(canonical),
                ):
                    stats.queued += 1
            except (OSError, ValueError):
                stats.errors += 1
    return stats
