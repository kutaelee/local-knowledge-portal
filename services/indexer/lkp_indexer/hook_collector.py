from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lkp.db import SessionLocal
from lkp.models import ActivityEvent, Document, HookSpoolEvent
from lkp.settings import Settings, get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from .activity_knowledge import finalize_pending_stops, project_from_paths
from .activity_retention import roll_up_activity_details
from .service_runtime import assert_mount_guards, service_pid

_LOW_SIGNAL_PROMPTS = {
    "",
    "?",
    "응",
    "네",
    "ㅇㅇ",
    "확인",
    "계속",
    "진행",
    "ok",
    "okay",
    "continue",
}
_EVIDENCE_COMMAND = re.compile(
    r"(?ix)\b("
    r"pytest|ruff|playwright|alembic|benchmark|hyperfine|"
    r"(?:pnpm|npm|yarn)\s+(?:run\s+)?(?:test|build|lint|typecheck)\b|"
    r"(?:backup|restore)(?:-test)?(?:\.ps1|\.sh)?\b|"
    r"docker\s+(?:build|restart|stop)\b|"
    r"docker\s+compose(?:\s+-[^\s]+\s+\S+)*\s+"
    r"(?:up|down|start|stop|restart|build|pull|run)\b|"
    r"git\s+commit\b"
    r")"
)
_REUSABLE_INSTRUCTION = re.compile(
    r"(?i)(fix|bug|error|fail|implement|develop|build|change|refactor|optim|performance|"
    r"load|incident|outage|recover|verify|test|benchmark|experiment|migrat|backup|"
    r"case|knowledge|record|collect|capture|workflow|pipeline|config|deploy|"
    r"restore|root cause|decision|runbook|오류|실패|수정|구현|추가|변경|개선|최적화|"
    r"성능|부하|장애|복구|검증|테스트|실험|마이그레이션|백업|복원|원인|재발|"
    r"회귀|설계|배포|빌드|결정|런북|개발|사례|지식|기록|수집|저장|워크플로우|"
    r"파이프라인|설정|운영)"
)
_MUTATING_TOOLS = {"apply_patch", "write_file", "edit_file"}
_last_retention_check = 0.0


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


_EXIT_CODE_LINE = re.compile(r"(?im)^\s*Exit code:\s*(-?\d+)\s*$")
_PATCH_FILE_LINE = re.compile(
    r"(?m)^\*{3}\s+(?:Add|Update|Delete) File:\s+(.+?)\s*$"
)
_CHANGE_STATUS_LINE = re.compile(r"(?m)^\s*[AMD]\s+(.+?)\s*$")


def _response_text(payload: dict[str, Any]) -> str:
    value = payload.get("tool_response") or payload.get("toolResponse")
    if isinstance(value, str):
        return value[:1_000_000]
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)[:1_000_000]
    return ""


def _transcript_tool_output(
    payload: dict[str, Any], sessions_root: Path | None
) -> str:
    if sessions_root is None:
        return ""
    transcript = payload.get("transcript_path")
    tool_use_id = payload.get("tool_use_id")
    if not isinstance(transcript, str) or not isinstance(tool_use_id, str):
        return ""
    normalized = transcript.replace("\\", "/")
    marker = "/.codex/sessions/"
    marker_index = normalized.casefold().find(marker)
    if marker_index < 0:
        return ""
    relative = normalized[marker_index + len(marker) :].lstrip("/")
    if not relative or ".." in Path(relative).parts:
        return ""
    root = sessions_root.resolve(strict=False)
    source = (root / relative).resolve(strict=False)
    try:
        source.relative_to(root)
        with source.open("rb") as handle:
            size = source.stat().st_size
            handle.seek(max(0, size - 4_000_000))
            raw = handle.read(4_000_000)
    except (FileNotFoundError, OSError, ValueError):
        return ""
    for line in reversed(raw.decode("utf-8", errors="replace").splitlines()):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        record = item.get("payload") if isinstance(item, dict) else None
        if not isinstance(record, dict) or record.get("call_id") != tool_use_id:
            continue
        output = record.get("output")
        return output[:1_000_000] if isinstance(output, str) else ""
    return ""


def _transcript_turn_instruction(
    payload: dict[str, Any],
    sessions_root: Path | None,
    turn_id: str | None,
    max_bytes: int = 8_000_000,
) -> str | None:
    if sessions_root is None or not turn_id:
        return None
    transcript = payload.get("transcript_path")
    if not isinstance(transcript, str):
        return None
    normalized = transcript.replace("\\", "/")
    marker = "/.codex/sessions/"
    marker_index = normalized.casefold().find(marker)
    if marker_index < 0:
        return None
    relative = normalized[marker_index + len(marker) :].lstrip("/")
    if not relative or ".." in Path(relative).parts:
        return None
    root = sessions_root.resolve(strict=False)
    source = (root / relative).resolve(strict=False)
    try:
        source.relative_to(root)
        with source.open("rb") as handle:
            size = source.stat().st_size
            handle.seek(max(0, size - max_bytes))
            raw = handle.read(max_bytes)
    except (FileNotFoundError, OSError, ValueError):
        return None

    active_turn: str | None = None
    messages: list[str] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or item.get("type") != "event_msg":
            continue
        record = item.get("payload")
        if not isinstance(record, dict):
            continue
        event_type = record.get("type")
        if event_type == "task_started":
            active_turn = str(record.get("turn_id") or "")
            continue
        if event_type == "task_complete":
            if str(record.get("turn_id") or "") == turn_id:
                break
            continue
        if event_type != "user_message" or active_turn != turn_id:
            continue
        message = record.get("message")
        if isinstance(message, str) and message.strip():
            messages.append(message.strip())
    if not messages:
        return None
    return "\n\n".join(messages)[:16000]


def _exit_code(
    payload: dict[str, Any], sessions_root: Path | None = None
) -> int | None:
    value = _nested(payload, "exit_code", "exitCode", "status_code")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value)
    for output in (_response_text(payload), _transcript_tool_output(payload, sessions_root)):
        match = _EXIT_CODE_LINE.search(output)
        if match:
            return int(match.group(1))
    return None


def _changed_files(payload: dict[str, Any], tool_name: str | None = None) -> list[str]:
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        return []
    found: list[str] = []

    def add(value: str) -> None:
        cleaned = value.strip().strip('"')
        if cleaned and cleaned not in found:
            found.append(cleaned)

    def walk(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for item_key, item in value.items():
                walk(item, str(item_key))
        elif isinstance(value, list):
            for item in value:
                walk(item, key)
        elif isinstance(value, str):
            if key in {"path", "file_path", "filename"}:
                add(value)
            for match in _PATCH_FILE_LINE.finditer(value):
                add(match.group(1))

    normalized_tool = (tool_name or "").casefold()
    if normalized_tool in _MUTATING_TOOLS:
        walk(tool_input)
        for match in _CHANGE_STATUS_LINE.finditer(_response_text(payload)):
            add(match.group(1))
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


def activity_signal(
    envelope: dict[str, Any], sessions_root: Path | None = None
) -> tuple[bool, list[str]]:
    payload = envelope.get("payload") or {}
    event_name = str(envelope.get("event_name") or "")
    reasons: list[str] = []
    if event_name in {"SessionStart", "SubagentStart"}:
        return False, ["lifecycle_only"]
    if event_name == "UserPromptSubmit":
        instruction = (_instruction(payload, event_name) or "").strip()
        normalized = instruction.casefold().rstrip(".!")
        if len(instruction) < 12 or normalized in _LOW_SIGNAL_PROMPTS:
            return False, ["acknowledgement_or_short_prompt"]
        if _REUSABLE_INSTRUCTION.search(instruction):
            return True, ["reusable_work_instruction"]
        return False, ["general_prompt_without_knowledge_signal"]
    if event_name == "PostToolUse":
        tool_name = (_tool_name(envelope, payload) or "").casefold()
        exit_code = _exit_code(payload, sessions_root)
        changed_files = _changed_files(payload, tool_name)
        command = _command(payload) or ""
        if exit_code not in {None, 0}:
            reasons.append("command_failure")
        if changed_files:
            reasons.append("changed_files")
        if tool_name in _MUTATING_TOOLS:
            reasons.append("mutating_tool")
        if _EVIDENCE_COMMAND.search(command):
            reasons.append("verification_or_operation_command")
        return bool(reasons), reasons or ["read_only_low_signal_tool"]
    if event_name in {"Stop", "SubagentStop"}:
        reported = (_reported_result(payload, event_name) or "").strip()
        if len(reported) >= 12:
            return True, ["reported_outcome"]
        return False, ["empty_or_short_outcome"]
    return False, ["unsupported_activity_signal"]


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


def envelope_to_activity(
    session: Session,
    envelope: dict[str, Any],
    sessions_root: Path | None = None,
    transcript_tail_bytes: int = 8_000_000,
) -> ActivityEvent:
    payload = envelope["payload"]
    event_name = str(envelope["event_name"])
    event_id = str(envelope["event_id"])
    existing = session.scalar(select(ActivityEvent).where(ActivityEvent.event_key == event_id))
    if existing:
        return existing
    tool_name = _tool_name(envelope, payload)
    exit_code = _exit_code(payload, sessions_root)
    changed_files = _changed_files(payload, tool_name)
    cwd = str(envelope.get("cwd") or payload.get("cwd") or "") or None
    turn_id = envelope.get("turn_id")
    instruction = _instruction(payload, event_name)
    if event_name in {"Stop", "SubagentStop"} and instruction is None:
        instruction = _transcript_turn_instruction(
            payload,
            sessions_root,
            str(turn_id) if turn_id else None,
            transcript_tail_bytes,
        )
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
        turn_id=turn_id,
        event_type=event_name,
        occurred_at=_parse_time(payload.get("timestamp") or envelope.get("received_at")),
        project_key=project_from_paths(changed_files, cwd),
        cwd=cwd,
        instruction=instruction,
        tool_name=tool_name,
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
            )
        ).all()
        for stop_event in completed:
            if stop_event.verification_status == "UNVERIFIED":
                stop_event.verified_result = (
                    f"{activity.tool_name or 'tool'} exit={activity.exit_code}"
                )
                stop_event.verification_status = "VERIFIED"
            metadata = dict(stop_event.metadata_json or {})
            pipeline = metadata.get("knowledge_pipeline") or {}
            if pipeline.get("reason") in {
                "no_meaningful_file_change",
                "no_successful_validation",
            }:
                metadata.pop("knowledge_pipeline", None)
                stop_event.metadata_json = metadata
    return activity


def collect_file(
    session: Session,
    path: Path,
    sessions_root: Path | None = None,
    transcript_tail_bytes: int = 8_000_000,
) -> bool:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    event_id = str(envelope["event_id"])
    existing = session.get(HookSpoolEvent, event_id)
    if existing:
        return False
    promote, signal_reasons = activity_signal(envelope, sessions_root)
    if promote and str(envelope.get("event_name") or "") in {"Stop", "SubagentStop"}:
        prior = session.scalar(
            select(ActivityEvent.id).where(
                ActivityEvent.session_id == str(envelope.get("session_id") or ""),
                ActivityEvent.turn_id == envelope.get("turn_id"),
            )
        )
        if prior is None:
            promote = False
            signal_reasons = ["outcome_without_selected_work_signal"]
    if not promote:
        return True
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
            payload_json={
                "signal_reasons": signal_reasons,
                "payload_bytes": envelope.get("payload_bytes"),
            },
            spool_path=str(path),
            status="promoted_activity",
            processed_at=datetime.now(timezone.utc),
        )
    )
    envelope_to_activity(
        session,
        envelope,
        sessions_root,
        transcript_tail_bytes,
    )
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
    global _last_retention_check
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
        paths = sorted(
            pending.glob("*.json"),
            key=lambda item: (item.stat().st_mtime_ns, item.name),
        )
        for path in paths:
            counts["seen"] += 1
            claimed = _claim(path)
            if claimed is None:
                continue
            try:
                with SessionLocal() as session:
                    changed = collect_file(
                        session,
                        claimed,
                        settings.codex_sessions_dir,
                        settings.knowledge_transcript_tail_bytes,
                    )
                    session.commit()
                claimed.unlink(missing_ok=True)
                counts["processed" if changed else "duplicates"] += 1
            except Exception:
                counts["failed"] += 1
                retry = root / "pending" / claimed.name
                retry.parent.mkdir(parents=True, exist_ok=True)
                os.replace(claimed, retry)
    try:
        with SessionLocal() as session:
            knowledge_counts = finalize_pending_stops(session, settings)
            rolled_up = 0
            now_monotonic = time.monotonic()
            if (
                now_monotonic - _last_retention_check
                >= settings.activity_retention_check_seconds
            ):
                rolled_up = roll_up_activity_details(
                    session,
                    retention_days=settings.activity_detail_retention_days,
                )
                _last_retention_check = now_monotonic
            session.commit()
        counts.update(
            {f"knowledge_{key}": value for key, value in knowledge_counts.items()}
        )
        counts["activity_details_rolled_up"] = rolled_up
    except Exception:
        counts["failed"] += 1
        counts["knowledge_failed"] = 1
    return counts


def watch(settings: Settings) -> None:
    assert_mount_guards(settings)
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
