from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lkp.db import SessionLocal
from lkp.models import ActivityEvent, Document, HookSpoolEvent, WorkerHeartbeat
from lkp.settings import Settings, get_settings
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .activity_knowledge import finalize_pending_stops, project_from_paths
from .activity_retention import roll_up_activity_details, roll_up_operational_details
from .artifact_evidence import ARTIFACT_EVIDENCE_VERSION, verify_report_artifacts
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
_AMBIENT_CONTEXT = re.compile(
    r"(?is)<(?:in-app-browser-context|environment_context|permissions\s+instructions|"
    r"apps_instructions|plugins_instructions|skills_instructions|recommended_plugins)\b[^>]*>.*?"
    r"</(?:in-app-browser-context|environment_context|permissions\s+instructions|"
    r"apps_instructions|plugins_instructions|skills_instructions|recommended_plugins)>"
)
_REQUEST_MARKER = re.compile(r"(?is)^\s*##\s*My request for Codex:\s*")
_last_retention_check = 0.0
_collector_started_at = datetime.now(timezone.utc)


def _parse_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _clean_instruction(value: str | None) -> str | None:
    """Exclude desktop-injected UI context from the activity's user intent."""

    cleaned = _AMBIENT_CONTEXT.sub("", value or "").strip()
    cleaned = _REQUEST_MARKER.sub("", cleaned).strip()
    return cleaned[:16000] or None


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


_EXIT_CODE_LINE = re.compile(
    r"(?im)^\s*(?:exit(?:[ _-]?code|\s+status)|return[ _-]?code)\s*[:=]\s*(-?\d+)\s*$"
)
_PATCH_FILE_LINE = re.compile(r"(?m)^\*{3}\s+(?:Add|Update|Delete) File:\s+(.+?)\s*$")
_CHANGE_STATUS_LINE = re.compile(r"(?m)^\s*[AMD]\s+(.+?)\s*$")


def _response_text(payload: dict[str, Any]) -> str:
    """Return only a bounded tool-result representation.

    Codex hook payloads have used both ``tool_response`` and ``tool_output``
    spellings across surfaces.  The collector does not treat a final assistant
    message as execution evidence: only the post-tool result is considered.
    """

    value = next(
        (
            payload.get(name)
            for name in (
                "tool_response",
                "toolResponse",
                "tool_output",
                "toolOutput",
                "tool_result",
                "toolResult",
            )
            if payload.get(name) is not None
        ),
        None,
    )
    if isinstance(value, str):
        return value[:1_000_000]
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)[:1_000_000]
    return ""


def _transcript_tool_output(payload: dict[str, Any], sessions_root: Path | None) -> str:
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
    return _clean_instruction("\n\n".join(messages))


def _transcript_turn_result(
    payload: dict[str, Any],
    sessions_root: Path | None,
    turn_id: str | None,
    max_bytes: int = 8_000_000,
) -> str | None:
    """Read the authoritative UTF-8 final result from the Codex transcript.

    Windows PowerShell 5.1 can decode redirected stdin with the active console
    code page. The transcript is the local UTF-8 source of truth, so Stop
    events prefer it over the hook payload and avoid persisting mojibake.
    """

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

    for line in reversed(raw.decode("utf-8", errors="replace").splitlines()):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict) or item.get("type") != "event_msg":
            continue
        record = item.get("payload")
        if (
            not isinstance(record, dict)
            or record.get("type") != "task_complete"
            or str(record.get("turn_id") or "") != turn_id
        ):
            continue
        result = record.get("last_agent_message")
        if isinstance(result, str) and result.strip():
            return result[:16000]
        return None
    return None


def _exit_code_with_source(
    payload: dict[str, Any], sessions_root: Path | None = None
) -> tuple[int | None, str | None]:
    """Extract an observed command exit code without trusting prose output.

    Direct structured fields win.  A bounded tool result and then the local
    Codex transcript are fallbacks for hook schemas that omit the field from
    the event envelope.  This deliberately returns no value for an assistant
    report, so a reported success/failure can never become verification.
    """

    value = _nested(
        payload,
        "exit_code",
        "exitCode",
        "exit_status",
        "exitStatus",
        "status_code",
        "statusCode",
        "return_code",
        "returnCode",
        "returncode",
    )
    if isinstance(value, int):
        return value, "payload_field"
    if isinstance(value, str) and value.lstrip("-").isdigit():
        return int(value), "payload_field"
    response = _response_text(payload)
    if response:
        match = _EXIT_CODE_LINE.search(response)
        if match:
            return int(match.group(1)), "tool_response"
    transcript_output = _transcript_tool_output(payload, sessions_root)
    if transcript_output:
        match = _EXIT_CODE_LINE.search(transcript_output)
        if match:
            return int(match.group(1)), "transcript"
    return None, None


def _exit_code(payload: dict[str, Any], sessions_root: Path | None = None) -> int | None:
    """Compatibility helper for callers and focused collector tests."""

    value, _ = _exit_code_with_source(payload, sessions_root)
    return value


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
    return _clean_instruction(value) if isinstance(value, str) else None


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
    if event_name == "LocalChatCompleted":
        user_message = str(payload.get("user_message") or "").strip()
        assistant_message = str(payload.get("assistant_message") or "").strip()
        if len(user_message) >= 12 and len(assistant_message) >= 12:
            return True, ["local_llm_chat_turn"]
        return False, ["empty_or_short_local_chat"]
    return False, ["unsupported_activity_signal"]


def _document_path_aliases(raw_path: str, cwd: str | None) -> tuple[str, ...]:
    """Map host hook paths to the canonical read-only paths stored by the watcher."""

    raw = raw_path.strip().strip('"').replace("\\", "/")
    base = (cwd or "").strip().strip('"').replace("\\", "/")
    if not re.match(r"^(?:[A-Za-z]:/|/|//)", raw) and base:
        raw = f"{base.rstrip('/')}/{raw}"
    raw = re.sub(r"/+", "/", raw)
    aliases: list[str] = []

    def add(value: str) -> None:
        normalized = posixpath.normpath(value)
        if normalized not in aliases:
            aliases.append(normalized)

    windows_repo = re.match(r"(?i)^[A-Za-z]:/Dev/Repos(?:/(.*))?$", raw)
    if windows_repo:
        suffix = windows_repo.group(1) or ""
        add(f"/sources/windows-repositories/{suffix}")
    mounted_windows_repo = re.match(r"(?i)^/mnt/c/Dev/Repos(?:/(.*))?$", raw)
    if mounted_windows_repo:
        suffix = mounted_windows_repo.group(1) or ""
        add(f"/sources/windows-repositories/{suffix}")
    wsl_unc = re.match(
        r"(?i)^/(?:/)?wsl(?:\.localhost|\$)/[^/]+(?P<path>/.*)$",
        raw,
    )
    if wsl_unc:
        add(wsl_unc.group("path"))
    if raw.startswith("/") and not wsl_unc:
        add(raw)
    return tuple(aliases)


def _document_versions(session: Session, changed_files: list[str], cwd: str | None) -> list:
    versions = []
    for raw_path in changed_files:
        aliases = _document_path_aliases(raw_path, cwd)
        if not aliases:
            continue
        row = session.scalar(
            select(Document).where(Document.canonical_path.in_(aliases))
        )
        if row and row.current_version_id and row.current_version_id not in versions:
            versions.append(row.current_version_id)
    return versions


def backfill_activity_document_versions(session: Session) -> dict[str, int]:
    """Relink historical host-path mutations after a source root is reconciled."""

    considered = 0
    linked = 0
    rows = session.scalars(
        select(ActivityEvent).where(
            ActivityEvent.event_type == "PostToolUse",
            func.cardinality(ActivityEvent.document_version_ids) == 0,
            func.cardinality(ActivityEvent.changed_files) > 0,
        )
    )
    for event in rows:
        considered += 1
        versions = _document_versions(session, list(event.changed_files or []), event.cwd)
        if not versions:
            continue
        event.document_version_ids = versions
        linked += 1
    session.flush()
    return {"considered": considered, "linked": linked}


def backfill_activity_report_artifacts(session: Session) -> dict[str, int]:
    """Verify historical report-linked assets once under the current verifier."""

    considered = 0
    with_artifacts = 0
    for event in session.scalars(
        select(ActivityEvent).where(
            ActivityEvent.event_type.in_(["Stop", "SubagentStop"]),
            ActivityEvent.reported_result.is_not(None),
        )
    ):
        metadata = dict(event.metadata_json or {})
        scan = dict(metadata.get("artifact_scan") or {})
        if scan.get("version") == ARTIFACT_EVIDENCE_VERSION:
            continue
        considered += 1
        artifacts = verify_report_artifacts(
            event.reported_result or "",
            occurred_at=event.occurred_at,
        )
        metadata["artifact_scan"] = {
            "version": ARTIFACT_EVIDENCE_VERSION,
            "verified_count": len(artifacts),
        }
        if artifacts:
            metadata["verified_artifacts"] = artifacts
            with_artifacts += 1
        event.metadata_json = metadata
    session.flush()
    return {"considered": considered, "with_artifacts": with_artifacts}


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
    if event_name == "LocalChatCompleted":
        project_key = str(payload.get("project_key") or "unassigned")[:200]
        activity = ActivityEvent(
            event_key=event_id,
            session_id=str(envelope.get("session_id") or ""),
            turn_id=envelope.get("turn_id"),
            event_type="LocalChat",
            occurred_at=_parse_time(
                str(payload.get("timestamp") or envelope.get("received_at") or "")
            ),
            project_key=project_key,
            cwd=None,
            instruction=str(payload.get("user_message") or "")[:16000] or None,
            changed_files=[],
            document_version_ids=[],
            reported_result=str(payload.get("assistant_message") or "")[:16000] or None,
            verification_status="UNVERIFIED",
            metadata_json={
                "activity_source": "local_llm_chat",
                "model": payload.get("model"),
                "payload_hash": envelope.get("payload_hash"),
                "reported_result_is_evidence": False,
                "source_metadata": payload.get("metadata") or {},
            },
        )
        session.add(activity)
        session.flush()
        return activity
    tool_name = _tool_name(envelope, payload)
    exit_code, exit_evidence_source = _exit_code_with_source(payload, sessions_root)
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
    reported_result = _reported_result(payload, event_name)
    if event_name in {"Stop", "SubagentStop"}:
        reported_result = _transcript_turn_result(
            payload,
            sessions_root,
            str(turn_id) if turn_id else None,
            transcript_tail_bytes,
        ) or reported_result
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
    occurred_at = _parse_time(payload.get("timestamp") or envelope.get("received_at"))
    activity_metadata = {
        "hook_status": envelope.get("status"),
        "permission_mode": payload.get("permission_mode"),
        "model": payload.get("model"),
        "payload_hash": envelope.get("payload_hash"),
        "exit_evidence_source": exit_evidence_source,
    }
    if event_name in {"Stop", "SubagentStop"} and reported_result:
        artifacts = verify_report_artifacts(
            reported_result,
            occurred_at=occurred_at,
        )
        activity_metadata["artifact_scan"] = {
            "version": ARTIFACT_EVIDENCE_VERSION,
            "verified_count": len(artifacts),
        }
        if artifacts:
            activity_metadata["verified_artifacts"] = artifacts
    activity = ActivityEvent(
        event_key=event_id,
        session_id=str(envelope.get("session_id") or ""),
        turn_id=turn_id,
        event_type=event_name,
        occurred_at=occurred_at,
        project_key=project_from_paths(changed_files, cwd),
        cwd=cwd,
        instruction=instruction,
        tool_name=tool_name,
        command=_command(payload),
        exit_code=exit_code,
        changed_files=changed_files,
        document_version_ids=_document_versions(session, changed_files, cwd),
        reported_result=reported_result,
        verified_result=verified_result,
        verification_status=verification_status,
        metadata_json=activity_metadata,
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
            artifact_backfill = backfill_activity_report_artifacts(session)
            knowledge_counts = finalize_pending_stops(session, settings)
            rolled_up = 0
            now_monotonic = time.monotonic()
            if now_monotonic - _last_retention_check >= settings.activity_retention_check_seconds:
                document_version_backfill = backfill_activity_document_versions(session)
                rolled_up = roll_up_activity_details(
                    session,
                    retention_days=settings.activity_detail_retention_days,
                )
                operational_rollup = roll_up_operational_details(
                    session,
                    terminal_job_retention_days=settings.terminal_job_detail_retention_days,
                    ingest_event_retention_days=settings.ingest_event_detail_retention_days,
                )
                _last_retention_check = now_monotonic
            else:
                document_version_backfill = {"considered": 0, "linked": 0}
                operational_rollup = {"jobs": 0, "events": 0}
            heartbeat = session.get(WorkerHeartbeat, "hook-collector")
            now = datetime.now(timezone.utc)
            if heartbeat is None:
                heartbeat = WorkerHeartbeat(
                    worker_id="hook-collector",
                    hostname=socket.gethostname(),
                    process_id=os.getpid(),
                    version=settings.pipeline_version,
                    started_at=_collector_started_at,
                    state="healthy",
                    processed_count=0,
                    failed_count=0,
                )
                session.add(heartbeat)
            elif heartbeat.process_id != os.getpid() or heartbeat.hostname != socket.gethostname():
                heartbeat.started_at = _collector_started_at
            heartbeat.hostname = socket.gethostname()
            heartbeat.process_id = os.getpid()
            heartbeat.version = settings.pipeline_version
            heartbeat.last_seen_at = now
            heartbeat.state = "error" if counts["failed"] else "healthy"
            heartbeat.processed_count += counts["processed"]
            heartbeat.failed_count += counts["failed"]
            heartbeat.metadata_json = {
                "service": "hook-collector",
                "spool_root_count": len(settings.hook_spool_roots),
                "last_poll": counts,
                "retention_rollup": {
                    "activity_details": rolled_up,
                    "terminal_jobs": operational_rollup["jobs"],
                    "ingest_events": operational_rollup["events"],
                },
                "document_version_backfill": document_version_backfill,
                "artifact_backfill": artifact_backfill,
            }
            session.commit()
        counts.update({f"knowledge_{key}": value for key, value in knowledge_counts.items()})
        counts["activity_details_rolled_up"] = rolled_up
        counts["document_versions_linked"] = document_version_backfill["linked"]
        counts["artifact_reports_verified"] = artifact_backfill["with_artifacts"]
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
