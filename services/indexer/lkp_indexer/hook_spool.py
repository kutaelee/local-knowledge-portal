from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lkp.redaction import redact_value

MAX_INPUT_BYTES = 1_048_576
ALLOWED_EVENTS = {
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "Stop",
    "SubagentStart",
    "SubagentStop",
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def sanitize(value: Any, *, key: str = "", depth: int = 0) -> Any:
    # Keep the legacy public helper, but use the same recursive display policy
    # as transcript capture and API serialization.
    return redact_value(value, key=key, depth=depth)


def _event_identity(payload: dict[str, Any], payload_hash: str) -> str:
    stable = {
        "session_id": payload.get("session_id"),
        "turn_id": payload.get("turn_id"),
        "hook_event_name": payload.get("hook_event_name"),
        "tool_use_id": payload.get("tool_use_id") or payload.get("tool_call_id"),
        "payload_hash": payload_hash,
    }
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def build_envelope(raw: bytes) -> tuple[dict[str, Any], str]:
    payload_hash = hashlib.sha256(raw).hexdigest()
    if len(raw) > MAX_INPUT_BYTES:
        envelope = {
            "schema_version": 1,
            "event_id": hashlib.sha256(f"oversized:{payload_hash}".encode()).hexdigest(),
            "received_at": utcnow(),
            "status": "oversized",
            "payload_hash": payload_hash,
            "payload_bytes": len(raw),
            "payload": {},
        }
        return envelope, "quarantine"
    try:
        decoded = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(decoded, dict):
            raise ValueError("hook payload must be an object")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        envelope = {
            "schema_version": 1,
            "event_id": hashlib.sha256(f"malformed:{payload_hash}".encode()).hexdigest(),
            "received_at": utcnow(),
            "status": "malformed",
            "payload_hash": payload_hash,
            "payload_bytes": len(raw),
            "error": type(exc).__name__,
            "payload": {},
        }
        return envelope, "quarantine"
    payload = sanitize(decoded)
    event_name = str(payload.get("hook_event_name") or "Unknown")
    status = "accepted" if event_name in ALLOWED_EVENTS else "unsupported_event"
    envelope = {
        "schema_version": 1,
        "event_id": _event_identity(payload, payload_hash),
        "received_at": utcnow(),
        "status": status,
        "payload_hash": payload_hash,
        "payload_bytes": len(raw),
        "event_name": event_name,
        "session_id": str(payload.get("session_id") or ""),
        "turn_id": str(payload.get("turn_id") or "") or None,
        "cwd": payload.get("cwd"),
        "tool_name": payload.get("tool_name"),
        "payload": payload,
    }
    return envelope, "pending" if status == "accepted" else "quarantine"


def atomic_spool(root: Path, envelope: dict[str, Any], bucket: str) -> Path:
    directory = root / bucket
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{envelope['event_id']}.json"
    if target.exists():
        return target
    encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{envelope['event_id']}.", suffix=".tmp", dir=directory
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()
    return target


def spool(raw: bytes, primary: Path, fallback: Path) -> Path:
    envelope, bucket = build_envelope(raw)
    try:
        return atomic_spool(primary, envelope, bucket)
    except OSError:
        envelope["used_fallback"] = True
        return atomic_spool(fallback, envelope, bucket)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spool-root", type=Path, required=True)
    parser.add_argument("--fallback-root", type=Path, required=True)
    args = parser.parse_args()
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    try:
        spool(raw, args.spool_root, args.fallback_root)
    except Exception:
        # The PowerShell wrapper writes a metadata-only last-resort envelope.
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
