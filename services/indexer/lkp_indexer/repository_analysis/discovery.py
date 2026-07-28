from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .domain import SourceFile

SUPPORTED_LANGUAGES = {
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".py": "Python",
    ".xml": "XML",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".properties": "properties",
    ".sql": "SQL",
    ".sh": "shell",
    ".bash": "shell",
    ".gradle": "Gradle",
    ".kts": "Kotlin",
    ".json": "JSON",
    ".toml": "TOML",
}
DEPENDENCY_FILENAMES = {
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "package.json",
    "requirements.txt",
    "pyproject.toml",
}
SKIP_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".next",
    ".venv",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    "coverage",
}
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "secrets.yaml",
    "secrets.yml",
}
SOURCE_ENCODINGS = ("utf-8-sig", "cp949", "euc-kr", "latin-1")


@dataclass(frozen=True)
class GitMetadata:
    commit: str | None
    branch: str | None
    dirty: bool
    origin_hash: str


@dataclass(frozen=True)
class DiscoveryResult:
    root: Path
    files: list[SourceFile]
    source_hash: str
    git: GitMetadata
    skipped: list[dict[str, str]]


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def git_metadata(root: Path) -> GitMetadata:
    commit = _git(root, "rev-parse", "HEAD")
    branch = _git(root, "branch", "--show-current")
    status = _git(root, "status", "--porcelain")
    origin = _git(root, "config", "--get", "remote.origin.url") or os.fspath(root)
    return GitMetadata(
        commit=commit,
        branch=branch,
        dirty=bool(status),
        origin_hash=hashlib.sha256(origin.encode("utf-8")).hexdigest(),
    )


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def validate_source_root(
    source_root: str | Path,
    *,
    allowed_roots: list[str | Path] | None = None,
) -> Path:
    root = Path(source_root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("repository analysis source must be a directory")
    if allowed_roots:
        resolved = [Path(item).expanduser().resolve(strict=True) for item in allowed_roots]
        if not any(root == allowed or _is_within(allowed, root) for allowed in resolved):
            raise ValueError("repository analysis source is outside the configured allowlist")
    return root


def _module_for(relative_path: Path) -> str | None:
    return relative_path.parts[0] if len(relative_path.parts) > 1 else None


def decode_source_bytes(raw: bytes) -> tuple[str, str]:
    for encoding in SOURCE_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("source", raw, 0, len(raw), "unsupported text encoding")


def discover(
    source_root: str | Path,
    *,
    allowed_roots: list[str | Path] | None = None,
    max_file_bytes: int = 2 * 1024 * 1024,
) -> DiscoveryResult:
    root = validate_source_root(source_root, allowed_roots=allowed_roots)
    records: list[SourceFile] = []
    skipped: list[dict[str, str]] = []
    fingerprint = hashlib.sha256()

    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in SKIP_DIRECTORIES for part in relative.parts):
            continue
        if path.is_symlink():
            skipped.append({"path": relative.as_posix(), "reason": "symlink"})
            continue
        if not path.is_file():
            continue
        if path.name.casefold() in SENSITIVE_NAMES or path.name.casefold().endswith(".key"):
            skipped.append({"path": relative.as_posix(), "reason": "sensitive_filename"})
            continue
        language = SUPPORTED_LANGUAGES.get(path.suffix.casefold())
        if not language and path.name not in DEPENDENCY_FILENAMES:
            continue
        size = path.stat().st_size
        if size > max_file_bytes:
            skipped.append({"path": relative.as_posix(), "reason": "oversized"})
            continue
        raw = path.read_bytes()
        if b"\x00" in raw[:8192]:
            skipped.append({"path": relative.as_posix(), "reason": "binary"})
            continue
        digest = hashlib.sha256(raw).hexdigest()
        try:
            text, _encoding = decode_source_bytes(raw)
        except UnicodeDecodeError:
            skipped.append({"path": relative.as_posix(), "reason": "non_utf8"})
            continue
        line_count = len(text.splitlines())
        record = SourceFile(
            relative_path=relative.as_posix(),
            content_hash=digest,
            language=language or "dependency-manifest",
            module=_module_for(relative),
            line_count=line_count,
            size_bytes=size,
        )
        records.append(record)
        fingerprint.update(record.relative_path.encode("utf-8"))
        fingerprint.update(b"\0")
        fingerprint.update(digest.encode("ascii"))
        fingerprint.update(b"\n")

    return DiscoveryResult(
        root=root,
        files=records,
        source_hash=fingerprint.hexdigest(),
        git=git_metadata(root),
        skipped=skipped,
    )
