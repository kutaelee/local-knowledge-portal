from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

from lkp.models import ActivityEvent, KnowledgeCandidate
from lkp.settings import Settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from .case_pages import materialize_case
from .knowledge import create_candidate, evaluate_gate, publish_candidate

_PROJECT_PATH = re.compile(
    r"(?ix)(?:[a-z]:/dev/repos|/home/[^/]+/src)/(?P<project>[^/\\]+)"
)
_MEANINGFUL_SUFFIXES = {
    ".c",
    ".cpp",
    ".cs",
    ".css",
    ".gd",
    ".go",
    ".h",
    ".hpp",
    ".html",
    ".ini",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".kt",
    ".md",
    ".mdx",
    ".properties",
    ".ps1",
    ".py",
    ".rs",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
_TEST_COMMAND = re.compile(
    r"(?ix)\b(pytest|playwright|test|tests|ruff|lint|typecheck|validate|check)\b"
)
_BUILD_COMMAND = re.compile(r"(?ix)\b(build|assemble|compile|docker\s+build)\b")
_COMPLETION = re.compile(
    r"(?i)(\bpass(?:ed)?\b|\bverified\b|\bcompleted?\b|\bfixed\b|"
    r"통과|검증\s*완료|구현\s*완료|수정\s*완료|복구\s*완료|완료했습니다|"
    r"재구축했습니다|구축했습니다|보정했습니다|고정했습니다)"
)
_PROGRESS_LEAD = re.compile(
    r"(?i)^\s*(현재\s*상태|진행\s*상황|진행\s*중|부분\s*완료|"
    r"요청\s*범위.*partial|아니요|초기\s*상태|아직\s*최종|"
    r"목표는\s*활성|partial\b|in\s+progress\b)"
)
_SECRET_ARGUMENT = re.compile(
    r"(?i)(password|passwd|token|secret|api[_-]?key|authorization)"
)
_LABELED_CAUSE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:원인|root\s+cause|cause)\s*[:：]\s*(.+?)\s*$"
)
_LABELED_SOLUTION = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:조치|해결|수정|solution|resolution|fix)\s*[:：]\s*(.+?)\s*$"
)


@dataclass(frozen=True)
class TurnSummary:
    project: str
    changed_files: tuple[str, ...]
    change_events: tuple[ActivityEvent, ...]
    failed_events: tuple[ActivityEvent, ...]
    successful_events: tuple[ActivityEvent, ...]
    instruction: str | None
    report: str


def project_from_paths(paths: list[str] | tuple[str, ...], fallback: str | None) -> str:
    projects: Counter[str] = Counter()
    for raw in paths:
        match = _PROJECT_PATH.search(raw.replace("\\", "/"))
        if match:
            projects[match.group("project")] += 1
    if projects:
        return projects.most_common(1)[0][0]
    if fallback:
        return PurePosixPath(fallback.replace("\\", "/")).name
    return "unknown-project"


def _first_line(value: str | None, *, limit: int) -> str:
    for line in (value or "").splitlines():
        cleaned = re.sub(r"^[#>*\-\s]+", "", line).strip()
        if cleaned:
            return cleaned[:limit]
    return ""


def _safe_command_family(command: str | None) -> str:
    value = command or ""
    if _SECRET_ARGUMENT.search(value):
        return "redacted validation command"
    if _TEST_COMMAND.search(value):
        return "test/lint/validation command"
    if _BUILD_COMMAND.search(value):
        return "build command"
    if re.search(r"(?i)\b(backup|restore)\b", value):
        return "backup/restore command"
    if re.search(r"(?i)\bdocker(?:\s+compose)?\b", value):
        return "container operation"
    if re.search(r"(?i)\bgit\s+commit\b", value):
        return "version-control commit"
    return "verification command"


def _meaningful_file(path: str) -> bool:
    normalized = path.replace("\\", "/")
    suffix = PurePosixPath(normalized).suffix.casefold()
    return suffix in _MEANINGFUL_SUFFIXES


def _event_evidence_type(event: ActivityEvent) -> str:
    command = event.command or ""
    if _BUILD_COMMAND.search(command):
        return "build_pass"
    if _TEST_COMMAND.search(command):
        return "test_pass"
    return "command_success"


def summarize_turn(session: Session, stop: ActivityEvent) -> TurnSummary:
    events = list(
        session.scalars(
            select(ActivityEvent)
            .where(
                ActivityEvent.session_id == stop.session_id,
                ActivityEvent.turn_id == stop.turn_id,
                ActivityEvent.occurred_at <= stop.occurred_at,
            )
            .order_by(ActivityEvent.occurred_at, ActivityEvent.created_at)
        )
    )
    change_events = tuple(
        item
        for item in events
        if item.event_type == "PostToolUse"
        and item.verification_status == "VERIFIED"
        and item.exit_code == 0
        and any(_meaningful_file(path) for path in (item.changed_files or []))
    )
    changed_files = tuple(
        dict.fromkeys(
            path
            for item in change_events
            for path in (item.changed_files or [])
            if _meaningful_file(path)
        )
    )
    failed_events = tuple(
        item
        for item in events
        if item.event_type == "PostToolUse"
        and item.verification_status == "VERIFIED"
        and item.exit_code is not None
        and item.exit_code != 0
    )
    successful_events = tuple(
        item
        for item in events
        if item.event_type == "PostToolUse"
        and item.verification_status == "VERIFIED"
        and item.exit_code == 0
        and item.command
        and (_TEST_COMMAND.search(item.command) or _BUILD_COMMAND.search(item.command))
    )
    instruction = stop.instruction or next(
        (
            item.instruction
            for item in reversed(events)
            if item.event_type == "UserPromptSubmit" and item.instruction
        ),
        None,
    )
    project = project_from_paths(changed_files, stop.project_key or stop.cwd)
    return TurnSummary(
        project=project,
        changed_files=changed_files,
        change_events=change_events,
        failed_events=failed_events,
        successful_events=successful_events,
        instruction=instruction,
        report=stop.reported_result or "",
    )


def _candidate_evidence(summary: TurnSummary) -> list[dict]:
    records: list[dict] = []
    for event in summary.change_events[:20]:
        files = [path for path in event.changed_files if _meaningful_file(path)]
        if not files:
            continue
        records.append(
            {
                "activity_id": event.id,
                "evidence_type": "code_change",
                "claim": f"Observed successful file mutation in {summary.project}",
                "locator": ", ".join(files[:8]),
                "verified_value": f"{len(files)} changed file(s); tool exit 0",
                "exit_code": 0,
                "verified": True,
            }
        )
    for event in summary.failed_events[:10]:
        records.append(
            {
                "activity_id": event.id,
                "evidence_type": "command_failure",
                "claim": f"Observed {_safe_command_family(event.command)} failure",
                "locator": _safe_command_family(event.command),
                "verified_value": f"observed exit_code={event.exit_code}",
                "exit_code": event.exit_code,
                "verified": True,
            }
        )
    for event in summary.successful_events[:10]:
        family = _safe_command_family(event.command)
        records.append(
            {
                "activity_id": event.id,
                "evidence_type": _event_evidence_type(event),
                "claim": f"Observed successful {family}",
                "locator": family,
                "verified_value": "observed exit_code=0",
                "exit_code": 0,
                "verified": True,
            }
        )
    return records


def _candidate_fields(summary: TurnSummary) -> dict[str, str]:
    report_title = _first_line(summary.report, limit=140)
    instruction_title = _first_line(summary.instruction, limit=240)
    title = report_title or instruction_title or f"{summary.project} verified change"
    goal = instruction_title or report_title or f"Verified development work in {summary.project}"
    file_names = [
        PurePosixPath(path.replace("\\", "/")).name
        for path in summary.changed_files[:12]
    ]
    validation_families = sorted(
        {_safe_command_family(item.command) for item in summary.successful_events}
    )
    approach = (
        f"Observed implementation in {summary.project}: "
        f"{len(summary.changed_files)} meaningful file(s) changed with successful tool exits."
    )
    solution = (
        f"Changed artifacts: {', '.join(file_names)}. "
        f"Observed validation: {', '.join(validation_families)}."
    )
    verified = (
        f"{len(summary.change_events)} successful change event(s), "
        f"{len(summary.successful_events)} successful validation event(s)"
    )
    if summary.failed_events:
        verified += f", {len(summary.failed_events)} observed failure event(s)"
    return {
        "title": f"[{summary.project}] {title}"[:300],
        "problem": goal,
        "symptom": f"{summary.project}: {goal}"[:1000],
        "root_cause": approach,
        "solution": solution,
        "verified_result": verified,
    }


def finalize_stop(
    session: Session,
    stop: ActivityEvent,
    settings: Settings,
) -> tuple[KnowledgeCandidate | None, str]:
    existing = session.scalar(
        select(KnowledgeCandidate).where(
            KnowledgeCandidate.metadata_json["source_stop_activity_id"].astext
            == str(stop.id)
        )
    )
    if existing is not None:
        return existing, "ALREADY_FINALIZED"

    summary = summarize_turn(session, stop)
    metadata = dict(stop.metadata_json or {})
    if (
        not summary.report.strip()
        or not summary.changed_files
        or not summary.successful_events
    ):
        metadata["knowledge_pipeline"] = {
            "state": "activity_only",
            "reason": (
                "missing_report"
                if not summary.report.strip()
                else "no_meaningful_file_change"
                if not summary.changed_files
                else "no_successful_validation"
            ),
        }
        stop.metadata_json = metadata
        return None, "ACTIVITY_ONLY"

    fields = _candidate_fields(summary)
    category = "error_resolution" if summary.failed_events else "implementation"
    labeled_cause = _LABELED_CAUSE.search(summary.report)
    labeled_solution = _LABELED_SOLUTION.search(summary.report)
    if category == "error_resolution" and labeled_cause and labeled_solution:
        fields["root_cause"] = labeled_cause.group(1)[:2000]
        fields["solution"] = labeled_solution.group(1)[:4000]
    auto_publish = bool(
        _COMPLETION.search(summary.report)
        and not _PROGRESS_LEAD.search(summary.report)
        and (
            category == "implementation"
            or bool(labeled_cause and labeled_solution)
        )
    )
    candidate = create_candidate(
        session,
        category=category,
        title=fields["title"],
        problem=fields["problem"],
        symptom=fields["symptom"],
        root_cause=fields["root_cause"],
        solution=fields["solution"],
        reported_result=summary.report[:16000],
        verified_result=fields["verified_result"],
        evidence=_candidate_evidence(summary),
        metadata={
            "auto_generated": True,
            "auto_publish_eligible": auto_publish,
            "extractor": "deterministic-activity-v1",
            "project": summary.project,
            "source_session_id": stop.session_id,
            "source_turn_id": stop.turn_id,
            "source_stop_activity_id": str(stop.id),
            "reported_result_is_evidence": False,
        },
    )
    gate = evaluate_gate(session, candidate)
    outcome = gate
    if gate == "VERIFIED" and auto_publish and settings.knowledge_auto_publish:
        case, outcome = publish_candidate(session, candidate)
        if case is not None:
            materialize_case(
                session,
                case,
                vault_dir=settings.vault_dir,
                pipeline_version=settings.pipeline_version,
            )

    metadata["knowledge_pipeline"] = {
        "state": candidate.status,
        "candidate_id": str(candidate.id),
        "outcome": outcome,
        "auto_publish_eligible": auto_publish,
    }
    stop.metadata_json = metadata
    return candidate, outcome


def finalize_pending_stops(session: Session, settings: Settings) -> dict[str, int]:
    counts = {
        "considered": 0,
        "activity_only": 0,
        "candidates": 0,
        "published": 0,
        "needs_review": 0,
    }
    stops = list(
        session.scalars(
            select(ActivityEvent)
            .where(
                ActivityEvent.event_type.in_(["Stop", "SubagentStop"]),
                ActivityEvent.metadata_json["knowledge_pipeline"].astext.is_(None),
            )
            .order_by(ActivityEvent.occurred_at)
        )
    )
    for stop in stops:
        pipeline_state = (stop.metadata_json or {}).get("knowledge_pipeline")
        if pipeline_state:
            continue
        counts["considered"] += 1
        candidate, outcome = finalize_stop(session, stop, settings)
        if candidate is None:
            counts["activity_only"] += 1
        else:
            counts["candidates"] += 1
            if candidate.status == "published":
                counts["published"] += 1
            elif candidate.status in {"needs_review", "verified"}:
                counts["needs_review"] += 1
    return counts
