from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

ARTIFACT_EVIDENCE_VERSION = "local-artifact-verifier-v1"
_HOST_ASSET = re.compile(
    r"(?i)^(?P<drive>[a-z]):[\\/]+AI[\\/]+Assets[\\/]+"
    r"(?P<scope>Source|Working|Final)[\\/]+(?P<relative>.+)$"
)
_MARKDOWN_TARGET = re.compile(
    r"(?i)\]\(\s*<?(?P<path>[a-z]:[\\/]+AI[\\/]+Assets[\\/]+"
    r"(?:Source|Working|Final)[^<>\r\n]*?)>?\s*\)"
)
_ANGLE_TARGET = re.compile(
    r"(?i)<(?P<path>[a-z]:[\\/]+AI[\\/]+Assets[\\/]+"
    r"(?:Source|Working|Final)[^<>\r\n]+)>"
)
_IMAGE_SOURCE = re.compile(r"""(?i)<img\b[^>]*\bsrc=["'](?P<src>[^"']+)["']""")
_SUPPORTED_SUFFIXES = {
    ".csv",
    ".html",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".png",
    ".txt",
    ".webp",
    ".yaml",
    ".yml",
}
_COMPARISON_NAME = re.compile(
    r"(?i)(review[-_ ]?board|comparison|compare|contact[-_ ]?sheet|validation)"
)
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
_MAX_HTML_BYTES = 2 * 1024 * 1024
_MAX_REFERENCED_ASSETS = 24


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _host_paths(report: str) -> tuple[str, ...]:
    found: list[str] = []
    for matcher in (_MARKDOWN_TARGET, _ANGLE_TARGET):
        for match in matcher.finditer(report):
            value = unquote(match.group("path")).strip().replace("\\", "/")
            if value not in found:
                found.append(value)
    return tuple(found[:20])


def _mounted_path(host_path: str, mount_root: Path) -> Path | None:
    match = _HOST_ASSET.match(host_path)
    if not match:
        return None
    candidate = (
        mount_root
        / match.group("scope")
        / Path(match.group("relative").replace("\\", "/"))
    ).resolve(strict=False)
    root = mount_root.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _within_event_window(path: Path, occurred_at: datetime | None) -> bool:
    if occurred_at is None:
        return True
    occurred = occurred_at.astimezone(timezone.utc)
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return occurred - timedelta(days=7) <= modified <= occurred + timedelta(minutes=15)


def _comparison_board(path: Path) -> dict[str, int | str] | None:
    if path.suffix.casefold() != ".html" or not _COMPARISON_NAME.search(path.name):
        return None
    if path.stat().st_size > _MAX_HTML_BYTES:
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    assets: list[Path] = []
    for match in _IMAGE_SOURCE.finditer(text):
        raw = unquote(match.group("src")).split("?", 1)[0].split("#", 1)[0]
        if not raw or "://" in raw or raw.startswith("data:"):
            continue
        target = (path.parent / raw).resolve(strict=False)
        try:
            target.relative_to(path.parent.resolve(strict=False))
        except ValueError:
            continue
        if target.is_file() and target.stat().st_size > 0:
            assets.append(target)
        if len(assets) >= _MAX_REFERENCED_ASSETS:
            break
    if len(assets) < 2:
        return None
    return {
        "evidence_type": "comparison_artifact",
        "referenced_asset_count": len(assets),
        "referenced_asset_bytes": sum(item.stat().st_size for item in assets),
    }


def verify_report_artifacts(
    report: str,
    *,
    occurred_at: datetime | None = None,
    mount_root: Path | None = None,
) -> list[dict[str, object]]:
    """Verify bounded, allowlisted local artifacts referenced by a final report."""

    root = mount_root or Path(
        os.getenv("LKP_AI_ASSETS_MOUNT", "/sources/ai-assets")
    )
    verified: list[dict[str, object]] = []
    for host_path in _host_paths(report):
        path = _mounted_path(host_path, root)
        if (
            path is None
            or not path.is_file()
            or path.suffix.casefold() not in _SUPPORTED_SUFFIXES
            or path.stat().st_size <= 0
            or path.stat().st_size > _MAX_ARTIFACT_BYTES
            or not _within_event_window(path, occurred_at)
        ):
            continue
        board = _comparison_board(path)
        evidence_type = (
            str(board["evidence_type"]) if board else "artifact_output"
        )
        item: dict[str, object] = {
            "verifier_version": ARTIFACT_EVIDENCE_VERSION,
            "evidence_type": evidence_type,
            "host_path": host_path,
            "size_bytes": path.stat().st_size,
            "modified_at": datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
            "sha256": _sha256(path),
        }
        if board:
            item.update(board)
        verified.append(item)
    return verified[:10]
