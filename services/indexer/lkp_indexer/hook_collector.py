from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lkp.db import SessionLocal
from lkp.models import ActivityEvent, Document, HookSpoolEvent
from lkp.settings import Settings, get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from .service_runtime import service_pid


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _nested(payload: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    for value in payload.values():
        if isinstance(value, dict):
            result = _nested(value, *names)
            if result is not None:
                return result
    return None


def _exit_code(payload: dict[str, Any]) -> int | None:
    value = _nested(payload, "exit_code", "exitCode", "status_code")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    return None


def _changed_files(payload: dict[str, Any]) -> list[str]:
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        return []
    found: list[str] = []

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for item_key, item in value.items():
                walk(item, str(item_key))
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
        elif isinstance(value, str) and key in {"path", "file_path", "filename"}:
            if value not in found:
                found.append(value)

    walk(tool_input)
    return found[:200]


def _command(payload: dict[str, Any]) -> str | None:
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if isinstance(tool_input, dict):
        value = tool_input.get("command") or tool_input.get("cmd")
        if isinstance(value, str):
            return value[:16000]
    return None


def _instruction(payload: dict[str, Any], event_name: str) -> str | None:
    if event_name != "UserPromptSubmit":
        return None
    value = _nested(payload, "prompt", "user_prompt", "message")
    return value[:16000] if isinstance(value, str) else None


def _reported_result(payload: dict[str, Any], event_name: str) -> str | None:
    if event_name not in {"Stop", "SubagentStop"}:
        return None
    value = _nested(payload, "last_assistant_message", "result", "stop_reason")
    return value[:16000] if isinstance(value, str) else None


def _tool_name(envelope: dict[str, Any], payload: dict[str, Any]) -> str | None:
    value = envelope.get("tool_name") or payload.get("tool_name") or payload.get("toolName")
    return str(value)[:200] if value else None


def _document_versions(
    session: Session, changed_files: list[str], cwd: str | None
) -> list:
    versions = []
    for raw_path in changed_files:
        path = Path(raw_path)
        if not path.is_absolute() and cwd:
            path = Path(cwd) / path
        canonical = str(path.resolve(strict=False))
        row = session.scalar(select(Document).where(Document.canonical_path == canonical))
        if row and row.current_version_id and row.current_version_id not in versions:
            versions.append(row.current_version_id)
    return versions


def envelope_to_activity(session: Session, envelope: dict[str, Any]) -> ActivityEvent:
    payload = envelope["payload"]
    event_name = str(envelope["event_name"])
    event_id = str(envelope["event_id"])
    existing = session.scalar(select(ActivityEvent).where(ActivityEvent.event_key == event_id))
    if existing:
        return existing
    exit_code = _exit_code(payload)
    changed_files = _changed_files(payload)
    cwd = str(envelope.get("cwd") or payload.get("cwd") or "") or None
    verified_result = None
    verification_status = "UNVERIFIED"
    if event_name == "PostToolUse" and exit_code is not None:
        verified_result = f"observed exit_code={exit_code}"
        verification_status = "VERIFIED"
    if event_name in {"Stop", "SubagentStop"}:
        prior = list(
            session.scalars(
                select(ActivityEvent).where(
                    ActivityEvent.session_id == str(envelope.get("session_id") or ""),
                    ActivityEvent.turn_id == envelope.get("turn_id"),
                    ActivityEvent.exit_code.is_not(None),
                )
            )
        )
        if prior:
            verified_result = "; ".join(
                f"{item.tool_name or 'tool'} exit={item.exit_code}" for item in prior[-10:]
            )
            verification_status = "VERIFIED"
    activity = ActivityEvent(
        event_key=event_id,
        session_id=str(envelope.get("session_id") or ""),
        turn_id=envelope.get("turn_id"),
        event_type=event_name,
        occurred_at=_parse_time(payload.get("timestamp") or envelope.get("received_at")),
        project_key=Path(cwd).name if cwd else None,
        cwd=cwd,
        instruction=_instruction(payload, event_name),
        tool_name=_tool_name(envelope, payload),
        command=_command(payload),
        exit_code=exit_code,
        changed_files=changed_files,
        document_version_ids=_document_versions(session, changed_files, cwd),
        reported_result=_reported_result(payload, event_name),
        verified_result=verified_result,
        verification_status=verification_status,
        metadata_json={
            "hook_status": envelope.get("status"),
            "permission_mode": payload.get("permission_mode"),
            "model": payload.get("model"),
            "payload_hash": envelope.get("payload_hash"),
        },
    )
    session.add(activity)
    session.flush()
    if event_name == "PostToolUse" and exit_code is not None:
        completed = session.scalars(
            select(ActivityEvent).where(
                ActivityEvent.session_id == activity.session_id,
                ActivityEvent.turn_id == activity.turn_id,
                ActivityEvent.event_type.in_(["Stop", "SubagentStop"]),
                ActivityEvent.verification_status == "UNVERIFIED",
            )
        ).all()
        for stop_event in completed:
            stop_event.verified_result = (
                f"{activity.tool_name or 'tool'} exit={activity.exit_code}"
            )
            stop_event.verification_status = "VERIFIED"
    return activity


def collect_file(session: Session, path: Path) -> bool:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    event_id = str(envelope["event_id"])
    existing = session.get(HookSpoolEvent, event_id)
    if existing:
        return False
    session.add(
        HookSpoolEvent(
            event_id=event_id,
            session_id=str(envelope.get("session_id") or ""),
            turn_id=envelope.get("turn_id"),
            event_name=str(envelope.get("event_name") or "Unknown"),
            occurred_at=_parse_time(envelope.get("received_at")),
            cwd=envelope.get("cwd"),
            tool_name=envelope.get("tool_name"),
            payload_hash=str(envelope["payload_hash"]),
            payload_json=envelope["payload"],
            spool_path=str(path),
            status="processed",
            processed_at=datetime.now(timezone.utc),
        )
    )
    envelope_to_activity(session, envelope)
    return True


def _claim(path: Path) -> Path | None:
    processing = path.parent.parent / "processing"
    processing.mkdir(parents=True, exist_ok=True)
    claimed = processing / path.name
    try:
        os.replace(path, claimed)
        return claimed
    except (FileNotFoundError, PermissionError):
        return None


def collect_once(settings: Settings) -> dict[str, int]:
    counts = {
        "seen": 0,
        "processed": 0,
        "duplicates": 0,
        "failed": 0,
        "recovered_claims": 0,
    }
    for root in settings.hook_spool_roots:
        processing = root / "processing"
        if processing.exists():
            stale_before = time.time() - settings.hook_claim_stale_seconds
            for claimed in processing.glob("*.json"):
                try:
                    if claimed.stat().st_mtime >= stale_before:
                        continue
                    retry = root / "pending" / claimed.name
                    retry.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(claimed, retry)
                    counts["recovered_claims"] += 1
                except (FileNotFoundError, PermissionError):
                    continue
        pending = root / "pending"
        if not pending.exists():
            continue
        for path in sorted(pending.glob("*.json")):
            counts["seen"] += 1
            claimed = _claim(path)
            if claimed is None:
                continue
            try:
                with SessionLocal() as session:
                    changed = collect_file(session, claimed)
                    session.commit()
                destination = root / "processed" / claimed.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(claimed, destination)
                counts["processed" if changed else "duplicates"] += 1
            except Exception:
                counts["failed"] += 1
                retry = root / "pending" / claimed.name
                retry.parent.mkdir(parents=True, exist_ok=True)
                os.replace(claimed, retry)
    return counts


def watch(settings: Settings) -> None:
    while True:
        collect_once(settings)
        time.sleep(max(1.0, settings.hook_collector_poll_seconds))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    with service_pid():
        if args.watch:
            watch(settings)
            return 0
        print(json.dumps(collect_once(settings)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
