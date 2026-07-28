"""Prepare content-addressed derived evidence without modifying source repositories."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .repository_analysis.discovery import decode_source_bytes

_SCHEMA_VERSION = 1
_MAX_ARCHIVE_MEMBER_BYTES = 2 * 1024 * 1024
_MAX_ARCHIVE_TOTAL_BYTES = 100 * 1024 * 1024


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _safe_member_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"unsafe archive member path: {name}")
    return path


def _write_blob(
    output_root: Path,
    *,
    source_hash: str,
    blob_key: str | None = None,
    writer: Any,
) -> tuple[Path, dict[str, Any]]:
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / "blobs" / (blob_key or source_hash)
    manifest_path = destination / "evidence-manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("source_sha256") != source_hash:
            raise RuntimeError("existing derived-evidence manifest hash mismatch")
        return destination, manifest
    if destination.exists():
        raise RuntimeError("incomplete derived-evidence blob already exists")

    temporary_root = output_root / "tmp"
    temporary_root.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f"{source_hash[:12]}-", dir=temporary_root)
    )
    try:
        manifest = writer(temporary)
        manifest_path = temporary / "evidence-manifest.json"
        manifest_path.write_text(_json(manifest) + "\n", encoding="utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination)
    except Exception:
        for path in sorted(temporary.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                path.rmdir()
        temporary.rmdir()
        raise
    return destination, manifest


def prepare_text_chunks(
    source: Path,
    output_root: Path,
    *,
    source_label: str,
    chunk_chars: int = 24_000,
) -> tuple[Path, dict[str, Any]]:
    raw = source.read_bytes()
    source_hash = _sha256(raw)
    text, encoding = decode_source_bytes(raw)
    if chunk_chars < 4_096 or chunk_chars > 100_000:
        raise ValueError("chunk_chars must be between 4096 and 100000")

    def writer(destination: Path) -> dict[str, Any]:
        chunks: list[dict[str, Any]] = []
        for index, start in enumerate(range(0, len(text), chunk_chars), start=1):
            end = min(len(text), start + chunk_chars)
            content = text[start:end]
            name = f"source-part-{index:04d}.js"
            payload = content.encode("utf-8")
            (destination / name).write_bytes(payload)
            chunks.append(
                {
                    "file": name,
                    "char_start": start,
                    "char_end": end,
                    "content_sha256": _sha256(payload),
                }
            )
        return {
            "schema_version": _SCHEMA_VERSION,
            "kind": "TEXT_CHUNKS",
            "source_label": source_label,
            "source_sha256": source_hash,
            "source_size_bytes": len(raw),
            "source_encoding": encoding,
            "source_characters": len(text),
            "chunk_characters": chunk_chars,
            "chunks": chunks,
            "content_preservation": "UTF-8 re-encoding only; chunks concatenate exactly",
        }

    destination, manifest = _write_blob(
        output_root,
        source_hash=source_hash,
        blob_key=f"{source_hash}-text-chunks-{chunk_chars}",
        writer=writer,
    )
    reconstructed = "".join(
        (destination / item["file"]).read_text(encoding="utf-8")
        for item in manifest["chunks"]
    )
    if reconstructed != text:
        raise RuntimeError("derived text chunks do not reconstruct the source")
    return destination, manifest


def prepare_tar_members(
    source: Path,
    output_root: Path,
    *,
    source_label: str,
) -> tuple[Path, dict[str, Any]]:
    raw = source.read_bytes()
    source_hash = _sha256(raw)

    def writer(destination: Path) -> dict[str, Any]:
        members: list[dict[str, Any]] = []
        total_size = 0
        with tarfile.open(source, mode="r:*") as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                relative = _safe_member_path(member.name)
                if member.size > _MAX_ARCHIVE_MEMBER_BYTES:
                    raise ValueError(f"archive member is oversized: {member.name}")
                total_size += member.size
                if total_size > _MAX_ARCHIVE_TOTAL_BYTES:
                    raise ValueError("archive expanded size exceeds the safety limit")
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError(f"archive member cannot be read: {member.name}")
                payload = handle.read()
                target = destination.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
                members.append(
                    {
                        "file": relative.as_posix(),
                        "size_bytes": len(payload),
                        "content_sha256": _sha256(payload),
                    }
                )
        if not members:
            raise ValueError("archive contains no regular files")
        return {
            "schema_version": _SCHEMA_VERSION,
            "kind": "TAR_MEMBERS",
            "source_label": source_label,
            "source_sha256": source_hash,
            "source_size_bytes": len(raw),
            "members": members,
        }

    return _write_blob(output_root, source_hash=source_hash, writer=writer)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("text-chunks", "tar-members"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-label", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--chunk-chars", type=int, default=24_000)
    args = parser.parse_args()
    source = args.source.expanduser().resolve(strict=True)
    output_root = args.output_root.expanduser().resolve()
    try:
        output_root.relative_to(source.parent)
    except ValueError:
        pass
    else:
        raise SystemExit("derived evidence output must be outside the source repository")
    if args.kind == "text-chunks":
        destination, manifest = prepare_text_chunks(
            source,
            output_root,
            source_label=args.source_label,
            chunk_chars=args.chunk_chars,
        )
    else:
        destination, manifest = prepare_tar_members(
            source,
            output_root,
            source_label=args.source_label,
        )
    print(
        json.dumps(
            {
                "output": str(destination),
                "source_sha256": manifest["source_sha256"],
                "kind": manifest["kind"],
                "items": len(manifest.get("chunks") or manifest.get("members") or []),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
