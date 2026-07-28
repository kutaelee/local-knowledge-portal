from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath

from lkp.models import (
    ActivityEvent,
    KnowledgeCandidate,
    ProjectJournalEntry,
    SystemSetting,
)
from lkp.settings import Settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from .case_pages import materialize_case
from .knowledge import (
    assess_knowledge_value,
    create_candidate,
    evaluate_gate,
    evaluate_quality,
    is_execution_tool,
    knowledge_key_terms,
    publish_candidate,
)
from .project_journal import (
    clean_journal_intent,
    clean_journal_summary,
    journal_title,
    materialize_project_journal,
)

_PROJECT_PATH = re.compile(
    r"(?ix)(?:[a-z]:/dev/repos|/home/[^/]+/src|//wsl\.localhost/[^/]+/home/[^/]+/src)/"
    r"(?P<project>[^/\\]+)"
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
_SECRET_ARGUMENT = re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key|authorization)")
_LABELED_CAUSE = re.compile(r"(?im)^\s*(?:[-*]\s*)?(?:원인|root\s+cause|cause)\s*[:：]\s*(.+?)\s*$")
_LABELED_SOLUTION = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:조치|해결|수정|solution|resolution|fix)\s*[:：]\s*(.+?)\s*$"
)
_LABELED_GOAL = re.compile(r"(?im)^\s*(?:[-*]\s*)?(?:목표|문제|goal|problem)\s*[:：]\s*(.+?)\s*$")
_LABELED_APPROACH = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:구현\s*방식|방식|접근|approach|implementation)\s*[:：]\s*(.+?)\s*$"
)
_LABELED_VERIFICATION = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:검증|확인\s*결과|verification|validated\s*result)\s*[:：]\s*(.+?)\s*$"
)
_REUSABLE_MEMO = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?재사용\s*메모(?:\s*\([^)]*\))?\s*[:：]\s*(.+?)\s*$"
)
_REUSABLE_MEMO_FIELD = re.compile(
    r"^\s*(상황|목표|문제|원인|조치|해결|수정|검증|방식|접근)\s*[:：—-]\s*(.+?)\s*$",
    re.IGNORECASE,
)
_JOURNAL_OPERATIONAL_PATH = re.compile(
    r"(?ix)(?:^|/)(?:"
    r"config|infra|scripts|db/migrations|docs/(?:adr|runbooks|architecture)|"
    r"AGENTS\.md$|README\.md$|compose(?:\.[^/]+)?\.ya?ml$|\.env\.example$"
    r")"
)
_PRESENTATION_ONLY_SUFFIXES = {".css", ".scss", ".sass", ".less"}
_REUSABLE_NARRATIVE = re.compile(
    r"(?i)(because|root cause|caused by|so that|instead of|trade-?off|"
    r"원인|때문|방지|대신|분리|결정|설계|구조|재시도|복구|안전장치|"
    r"동시에|트랜잭션|lease|idempoten|race|timeout|deadlock)"
)
_REFERENCE_KEYS = {
    "knowledge_references",
    "knowledge_refs",
    "rag_context",
    "citations",
    "provenance",
}
_REFERENCE_FIELDS = {
    "document_id",
    "document_version_id",
    "chunk_id",
    "source_root",
    "canonical_path",
    "relative_path",
    "start_line",
    "end_line",
    "content_hash",
    "indexed_at",
    "retrieval_score",
    "score",
}
_JOURNAL_CANDIDATE_REASSESSMENT_KEY = "knowledge.journal_candidate_reassessment.v1"
_JOURNAL_CANDIDATE_REASSESSMENT_VERSION = "journal-backed-editorial-v1"
_SEMANTIC_CANDIDATE_REASSESSMENT_KEY = "knowledge.semantic_candidate_reassessment.v2"
_SEMANTIC_CANDIDATE_REASSESSMENT_VERSION = "reported-provenance-editorial-v2"
_PROJECT_JOURNAL_REASSESSMENT_KEY = "project_journal.significance_reassessment.v2"
_PROJECT_JOURNAL_REASSESSMENT_VERSION = "observed-change-journal-v2"
_PROJECT_JOURNAL_SCOPE_REASSESSMENT_KEY = "project_journal.scope_reassessment.v3"
_PROJECT_JOURNAL_SCOPE_REASSESSMENT_VERSION = "session-anchor-project-scope-v3"


@dataclass(frozen=True)
class TurnSummary:
    project: str
    project_scope_verified: bool
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
        match = _PROJECT_PATH.search(fallback.replace("\\", "/"))
        if match:
            return match.group("project")
        return PurePosixPath(fallback.replace("\\", "/")).name
    return "unknown-project"


def _project_for_path(path: str) -> str | None:
    """Return a repository key only when the path is inside a known source repo."""

    match = _PROJECT_PATH.search(path.replace("\\", "/"))
    return match.group("project") if match else None


def _session_anchor_project(session: Session, session_id: str) -> str | None:
    """Return the first canonical repository used by a Codex session."""

    for cwd in session.scalars(
        select(ActivityEvent.cwd)
        .where(
            ActivityEvent.session_id == session_id,
            ActivityEvent.cwd.is_not(None),
        )
        .order_by(ActivityEvent.occurred_at, ActivityEvent.created_at)
    ):
        project = _project_for_path(cwd or "")
        if project:
            return project
    return None


def _project_scoped_files(
    paths: tuple[str, ...], project: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Keep a project journal readable when one turn touches global files too.

    A Codex turn can legitimately update a repository and a shared Windows/WSL
    configuration. The journal belongs to one project, so the visible file
    list must not make global operational files look like project source. The
    complete original list remains on immutable ActivityEvent rows; related
    paths are retained in entry metadata for provenance.
    """

    scoped = tuple(path for path in paths if _project_for_path(path) == project)
    if not scoped:
        # A source root can intentionally be outside conventional repos/src
        # locations. In that case show the verified change, but do not claim a
        # stronger repository boundary than we can prove.
        return paths, ()
    related = tuple(path for path in paths if path not in scoped)
    return scoped, related


def _first_line(value: str | None, *, limit: int) -> str:
    for line in (value or "").splitlines():
        cleaned = re.sub(r"^[#>*\-\s]+", "", line).strip()
        if cleaned:
            return cleaned[:limit]
    return ""


def _reusable_memo_fields(report: str) -> dict[str, str]:
    """Parse the optional one-line global Codex memo without trusting it as proof."""

    match = _REUSABLE_MEMO.search(report)
    if not match:
        return {}
    result: dict[str, str] = {}
    aliases = {
        "상황": "goal", "목표": "goal", "문제": "goal",
        "원인": "cause", "조치": "solution", "해결": "solution", "수정": "solution",
        "검증": "verification", "방식": "approach", "접근": "approach",
    }
    for part in re.split(r"\s*[|｜]\s*", match.group(1)):
        field = _REUSABLE_MEMO_FIELD.match(part)
        if field is None:
            continue
        key = aliases[field.group(1)]
        result.setdefault(key, field.group(2)[:4000])
    return result


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


def _is_execution_event(event: ActivityEvent) -> bool:
    """Accept test/build evidence only from a command execution tool.

    Edit payloads can contain paths such as ``tests/test_worker.py`` or even
    command examples. Matching their raw payload as if it were an executed
    command would turn a successful file edit into false validation evidence.
    """

    return is_execution_tool(event.tool_name)


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
        and _is_execution_event(item)
    )
    successful_events = tuple(
        item
        for item in events
        if item.event_type == "PostToolUse"
        and item.verification_status == "VERIFIED"
        and item.exit_code == 0
        and item.command
        and _is_execution_event(item)
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
    changed_path_projects = {
        project
        for path in changed_files
        if (project := _project_for_path(path)) is not None
    }
    session_anchor = _session_anchor_project(session, stop.session_id)
    current_cwd_project = _project_for_path(stop.cwd or "")
    project = project_from_paths(
        changed_files,
        session_anchor or current_cwd_project or stop.project_key or stop.cwd,
    )
    return TurnSummary(
        project=project,
        project_scope_verified=bool(
            changed_path_projects or session_anchor or current_cwd_project
        ),
        changed_files=changed_files,
        change_events=change_events,
        failed_events=failed_events,
        successful_events=successful_events,
        instruction=instruction,
        report=stop.reported_result or "",
    )


def _candidate_evidence(summary: TurnSummary, content_language: str = "ko") -> list[dict]:
    records: list[dict] = []
    korean = content_language == "ko"
    for event in summary.change_events[:20]:
        files = [path for path in event.changed_files if _meaningful_file(path)]
        if not files:
            continue
        records.append(
            {
                "activity_id": event.id,
                "evidence_type": "code_change",
                "claim": (
                    f"{summary.project}에서 성공한 파일 변경을 관측했습니다"
                    if korean
                    else f"Observed successful file mutation in {summary.project}"
                ),
                "locator": ", ".join(files[:8]),
                "verified_value": (
                    f"변경 파일 {len(files)}개, 도구 exit code 0"
                    if korean
                    else f"{len(files)} changed file(s); tool exit 0"
                ),
                "exit_code": 0,
                "verified": True,
            }
        )
    for event in summary.failed_events[:10]:
        records.append(
            {
                "activity_id": event.id,
                "evidence_type": "command_failure",
                "claim": (
                    f"{_safe_command_family(event.command)} 실패를 관측했습니다"
                    if korean
                    else f"Observed {_safe_command_family(event.command)} failure"
                ),
                "locator": _safe_command_family(event.command),
                "verified_value": (
                    f"관측된 exit_code={event.exit_code}"
                    if korean
                    else f"observed exit_code={event.exit_code}"
                ),
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
                "claim": (
                    f"성공한 {family} 실행을 관측했습니다"
                    if korean
                    else f"Observed successful {family}"
                ),
                "locator": family,
                "verified_value": ("관측된 exit_code=0" if korean else "observed exit_code=0"),
                "exit_code": 0,
                "verified": True,
            }
        )
    return records


def _candidate_fields(summary: TurnSummary, content_language: str = "ko") -> dict[str, str]:
    report_title = _first_line(summary.report, limit=140)
    instruction_title = _first_line(summary.instruction, limit=240)
    korean = content_language == "ko"
    title = (
        report_title
        or instruction_title
        or (f"{summary.project} 검증 작업" if korean else f"{summary.project} verified change")
    )
    goal = (
        instruction_title
        or report_title
        or (
            f"{summary.project}에서 검증된 개발 작업"
            if korean
            else f"Verified development work in {summary.project}"
        )
    )
    file_names = [
        PurePosixPath(path.replace("\\", "/")).name for path in summary.changed_files[:12]
    ]
    validation_families = sorted(
        {_safe_command_family(item.command) for item in summary.successful_events}
    )
    if korean:
        approach = (
            f"{summary.project}에서 의미 있는 파일 {len(summary.changed_files)}개의 "
            "성공한 변경은 확인됐지만, 재사용 가능한 원인이나 구현 결정은 "
            "아직 구조화되지 않았습니다."
        )
        solution = (
            f"변경 파일: {', '.join(file_names)}. "
            f"관측된 검증: {', '.join(validation_families)}. "
            "이 목록만으로 정식 지식 사례를 발행하지 않습니다."
        )
        verified = (
            f"성공한 변경 이벤트 {len(summary.change_events)}건, "
            f"성공한 검증 이벤트 {len(summary.successful_events)}건"
        )
    else:
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
        verified += (
            f", 관측된 실패 이벤트 {len(summary.failed_events)}건"
            if korean
            else f", {len(summary.failed_events)} observed failure event(s)"
        )
    return {
        "title": f"[{summary.project}] {title}"[:300],
        "problem": goal,
        "symptom": f"{summary.project}: {goal}"[:1000],
        "root_cause": approach,
        "solution": solution,
        "verified_result": verified,
    }


def _journal_significance(summary: TurnSummary) -> list[str]:
    """Return explicit reasons a change belongs in the project journal.

    This gate intentionally answers a different question from reusable knowledge:
    whether a verified change materially updates a project's development history.
    """

    if (
        not summary.project_scope_verified
        or not summary.report.strip()
        or not summary.changed_files
        or not summary.change_events
    ):
        return []
    normalized = [path.replace("\\", "/") for path in summary.changed_files]
    suffixes = {PurePosixPath(path).suffix.casefold() for path in normalized}
    reasons: list[str] = []
    if any(_JOURNAL_OPERATIONAL_PATH.search(path) for path in normalized):
        reasons.append("operational_or_configuration_change")
    if summary.failed_events and summary.successful_events:
        reasons.append("verified_failure_and_recovery")
    elif summary.failed_events:
        reasons.append("observed_failure_with_file_change")
    if len(normalized) >= 2:
        reasons.append("multi_artifact_change")
    if _COMPLETION.search(summary.report) and not _PROGRESS_LEAD.search(summary.report):
        reasons.append(
            "reported_completion_with_execution_evidence"
            if summary.successful_events
            else "reported_completion_with_observed_change"
        )
    if not reasons and _has_reusable_report_detail(summary):
        reasons.append("material_change_with_reported_outcome")
    if (
        suffixes
        and suffixes <= _PRESENTATION_ONLY_SUFFIXES
        and "operational_or_configuration_change" not in reasons
        and "verified_failure_and_recovery" not in reasons
    ):
        return []
    return list(dict.fromkeys(reasons))


def _candidate_category(summary: TurnSummary, journal_reasons: list[str]) -> str:
    if summary.failed_events:
        return "error_resolution"
    searchable = f"{summary.instruction or ''} {summary.report}".casefold()
    if any(
        token in searchable
        for token in (
            "performance",
            "latency",
            "throughput",
            "cpu",
            "vram",
            "성능",
            "지연",
            "처리량",
            "부하",
        )
    ):
        return "performance"
    if "operational_or_configuration_change" in journal_reasons:
        return "operations"
    return "implementation"


def _reported_sources(summary: TurnSummary) -> list[dict[str, str]]:
    """Keep author-reported semantics without misclassifying them as proof."""

    sources: list[dict[str, str]] = []
    if summary.report.strip():
        sources.append(
            {
                "kind": "final_report",
                "text": summary.report.strip()[:16000],
                "verification": "REPORTED_NOT_VERIFIED",
            }
        )
    if summary.instruction and summary.instruction.strip():
        sources.append(
            {
                "kind": "user_instruction",
                "text": summary.instruction.strip()[:8000],
                "verification": "REPORTED_INTENT",
            }
        )
    return sources


def _has_reusable_report_detail(summary: TurnSummary) -> bool:
    report = re.sub(r"\s+", " ", summary.report).strip()
    if len(report) >= 160:
        return True
    if len(report) >= 80 and _REUSABLE_NARRATIVE.search(report):
        return True
    return bool(summary.failed_events and len(report) >= 60)


def _extract_knowledge_references(events: tuple[ActivityEvent, ...]) -> list[dict]:
    references: list[dict] = []
    seen: set[str] = set()

    def visit(value: object, *, enabled: bool = False) -> None:
        if isinstance(value, list):
            for item in value[:100]:
                visit(item, enabled=enabled)
            return
        if not isinstance(value, dict):
            return
        reference = {
            key: value[key]
            for key in _REFERENCE_FIELDS
            if key in value and isinstance(value[key], (str, int, float))
        }
        if enabled and any(
            key in reference for key in ("document_id", "chunk_id", "canonical_path")
        ):
            if "retrieval_score" not in reference and "score" in reference:
                reference["retrieval_score"] = reference.pop("score")
            fingerprint = repr(sorted(reference.items()))
            if fingerprint not in seen:
                seen.add(fingerprint)
                references.append(reference)
        for key, child in value.items():
            visit(child, enabled=enabled or str(key).casefold() in _REFERENCE_KEYS)

    for event in events:
        visit(event.metadata_json or {})
    return references[:50]


def finalize_project_journal(
    session: Session,
    stop: ActivityEvent,
    summary: TurnSummary,
    settings: Settings,
) -> ProjectJournalEntry | None:
    existing = session.scalar(
        select(ProjectJournalEntry).where(
            ProjectJournalEntry.source_stop_activity_id == stop.id
        )
    )
    if existing is not None:
        return existing
    metadata = dict(stop.metadata_json or {})
    reasons = _journal_significance(summary)
    if not reasons:
        metadata["project_journal"] = {
            "state": "activity_only",
            "reason": "not_a_significant_verified_project_change",
        }
        stop.metadata_json = metadata
        return None
    all_events = (
        summary.change_events + summary.failed_events + summary.successful_events
    )
    references = _extract_knowledge_references(all_events)
    failures = [
        {
            "activity_id": str(event.id),
            "command_family": _safe_command_family(event.command),
            "exit_code": event.exit_code,
        }
        for event in summary.failed_events[:20]
    ]
    verification = [
        {
            "activity_id": str(event.id),
            "command_family": _safe_command_family(event.command),
            "evidence_type": _event_evidence_type(event),
            "exit_code": event.exit_code,
        }
        for event in summary.successful_events[:20]
    ]
    memo_fields = _reusable_memo_fields(summary.report)
    labeled_resolution = (
        _LABELED_SOLUTION.search(summary.report)
        or _LABELED_APPROACH.search(summary.report)
        or _LABELED_CAUSE.search(summary.report)
    )
    # A normal implementation must not be presented as a failure-and-fix story.
    # For an observed failed command, retain a labelled resolution when present;
    # otherwise describe only the post-failure execution evidence we actually
    # have, not an inferred root cause or fix.
    resolution = ""
    resolution_evidence = "not_applicable"
    if failures:
        if labeled_resolution or memo_fields.get("solution"):
            resolution = (
                labeled_resolution.group(1)[:4000]
                if labeled_resolution
                else memo_fields["solution"]
            )
            resolution_evidence = "reported_label_with_observed_failure"
        elif summary.successful_events:
            resolution = (
                "실패 뒤 성공 종료한 테스트·빌드·검증 명령이 관측되었습니다. "
                "정확한 원인과 코드 조치는 별도 구조화 메모가 없으면 단정하지 않습니다."
                if settings.knowledge_content_language == "ko"
                else (
                    "A successful test, build, or validation command was observed after the "
                    "failure. The exact cause and code change are not asserted without a "
                    "structured note."
                )
            )
            resolution_evidence = "post_failure_validation_only"
        else:
            resolution = (
                "명령 실패는 관측됐지만, 같은 turn에서 성공적인 복구 검증은 관측되지 않았습니다."
                if settings.knowledge_content_language == "ko"
                else (
                    "A command failure was observed, but no successful recovery validation "
                    "was observed in the same turn."
                )
            )
            resolution_evidence = "failure_without_recovery_validation"
    searchable = f"{summary.instruction} {summary.report}".casefold()
    if failures:
        work_type = "error_resolution"
    elif any(
        token in searchable
        for token in ("cpu", "latency", "performance", "load", "성능", "부하", "지연")
    ):
        work_type = "performance"
    elif "operational_or_configuration_change" in reasons:
        work_type = "operations"
    else:
        work_type = "implementation"
    journal_intent = clean_journal_intent(
        summary.instruction,
        korean=settings.knowledge_content_language == "ko",
    )
    journal_summary = clean_journal_summary(summary.report)
    journal_files, related_operational_files = _project_scoped_files(
        summary.changed_files, summary.project
    )
    title = journal_title(
        summary.project,
        intent=journal_intent,
        report=journal_summary,
        changed_files=list(journal_files),
        failures=bool(failures),
        operational="operational_or_configuration_change" in reasons,
        korean=settings.knowledge_content_language == "ko",
    )
    entry = ProjectJournalEntry(
        source_stop_activity_id=stop.id,
        project_key=summary.project,
        occurred_at=stop.occurred_at,
        title=title[:500],
        intent=journal_intent[:8000],
        change_summary=journal_summary[:16000],
        failures_json=failures,
        resolution=resolution,
        verification_json=verification,
        changed_files=list(journal_files[:200]),
        knowledge_references_json=references,
        significance_reasons=reasons,
        verification_status=(
            "VERIFIED" if summary.successful_events else "OBSERVED_CHANGE"
        ),
        metadata_json={
            "source_session_id": stop.session_id,
            "source_turn_id": stop.turn_id,
            "reported_result_is_evidence": False,
            "journal_policy": "significant-change-v1",
            "journal_presentation_version": "v2",
            "raw_activity_preserved": True,
            "related_operational_files": list(related_operational_files[:200]),
            "project_scope": "repository_paths_only" if related_operational_files else "all_paths",
            "knowledge_reference_count": len(references),
            "work_type": work_type,
            "failure_resolution_evidence": resolution_evidence,
            "validation_state": (
                "test_or_build_observed"
                if summary.successful_events
                else "file_change_observed_without_test_or_build"
            ),
        },
    )
    session.add(entry)
    session.flush()
    metadata["project_journal"] = {
        "state": "recorded",
        "entry_id": str(entry.id),
        "verification_status": entry.verification_status,
        "significance_reasons": reasons,
        "materialization": "batched_by_project",
    }
    stop.metadata_json = metadata
    return entry


def finalize_stop(
    session: Session,
    stop: ActivityEvent,
    settings: Settings,
) -> tuple[KnowledgeCandidate | None, str]:
    existing = session.scalar(
        select(KnowledgeCandidate).where(
            KnowledgeCandidate.metadata_json["source_stop_activity_id"].astext == str(stop.id)
        )
    )
    if existing is not None:
        return existing, "ALREADY_FINALIZED"

    summary = summarize_turn(session, stop)
    metadata = dict(stop.metadata_json or {})
    if not summary.report.strip() or not summary.changed_files or not summary.successful_events:
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

    content_language = settings.knowledge_content_language
    fields = _candidate_fields(summary, content_language)
    journal_reasons = _journal_significance(summary)
    category = _candidate_category(summary, journal_reasons)
    memo_fields = _reusable_memo_fields(summary.report)
    labeled_cause = _LABELED_CAUSE.search(summary.report)
    labeled_solution = _LABELED_SOLUTION.search(summary.report)
    labeled_goal = _LABELED_GOAL.search(summary.report)
    labeled_approach = _LABELED_APPROACH.search(summary.report)
    labeled_verification = _LABELED_VERIFICATION.search(summary.report)
    cause = labeled_cause.group(1)[:2000] if labeled_cause else memo_fields.get("cause")
    solution = labeled_solution.group(1)[:4000] if labeled_solution else memo_fields.get("solution")
    goal = labeled_goal.group(1)[:2000] if labeled_goal else memo_fields.get("goal")
    approach = labeled_approach.group(1)[:4000] if labeled_approach else memo_fields.get("approach")
    verification = (
        labeled_verification.group(1)[:4000]
        if labeled_verification
        else memo_fields.get("verification")
    )
    structured_knowledge = False
    if category == "error_resolution" and cause and solution:
        fields["root_cause"] = cause
        fields["solution"] = solution
        structured_knowledge = True
    elif (
        category == "implementation" and goal and approach and verification
    ):
        fields["problem"] = goal
        fields["symptom"] = goal
        fields["root_cause"] = approach
        fields["solution"] = approach
        fields["verified_result"] = verification
        structured_knowledge = True
    # Structured reusable reports and failure/fix turns keep their established
    # knowledge path.  The journal-editorial route is only for an otherwise
    # generic, verified operational/configuration change that would formerly
    # have been discarded before the local editor could assess it.
    journal_backed = bool(
        not structured_knowledge
        and journal_reasons
        and _has_reusable_report_detail(summary)
    )
    # A development journal may retain a material verified project change, but
    # a canonical knowledge candidate needs a reusable decision or a
    # failure/cause/solution trail. Do not turn a generic completion plus a
    # changed-file inventory into a review queue item.
    if not structured_knowledge and not journal_backed:
        metadata["knowledge_pipeline"] = {
            "state": "activity_only",
            "reason": "reusable_explanation_missing",
            "reported_result_is_evidence": False,
        }
        stop.metadata_json = metadata
        return None, "ACTIVITY_ONLY"
    auto_publish = bool(
        _COMPLETION.search(summary.report)
        and not _PROGRESS_LEAD.search(summary.report)
        and structured_knowledge
    )
    candidate_evidence = _candidate_evidence(summary, content_language)
    candidate_metadata = {
        "auto_generated": True,
        "auto_publish_eligible": auto_publish,
        "structured_knowledge": structured_knowledge,
        "journal_backed": journal_backed,
        "journal_significance_reasons": journal_reasons,
        "content_language": content_language,
        "approval_policy": "human_review",
        "extractor": "deterministic-activity-v2",
        "project": summary.project,
        "source_session_id": stop.session_id,
        "source_turn_id": stop.turn_id,
        "source_stop_activity_id": str(stop.id),
        "reported_result_is_evidence": False,
        "reported_sources": _reported_sources(summary),
        "reported_source_policy": (
            "reported context may explain intent/cause/decision, but verification and "
            "measured outcomes require E-prefixed execution evidence"
        ),
        "semantic_dedup": {
            "state": "pending_gpu_vector_check",
            "key_terms": knowledge_key_terms(
                fields["problem"], fields["root_cause"], fields["solution"]
            ),
            "policy": "key_terms_then_pgvector-v1",
        },
    }
    value_assessment = assess_knowledge_value(
        category=category,
        problem=fields["problem"],
        root_cause=fields["root_cause"],
        solution=fields["solution"],
        evidence=candidate_evidence,
        metadata=candidate_metadata,
    )
    candidate_metadata["knowledge_value"] = value_assessment
    if value_assessment["tier"] == "activity_only":
        metadata["knowledge_pipeline"] = {
            "state": "activity_only",
            "reason": "knowledge_value_harness_rejected",
            "knowledge_value": value_assessment,
        }
        stop.metadata_json = metadata
        return None, "ACTIVITY_ONLY"

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
        evidence=candidate_evidence,
        metadata=candidate_metadata,
    )
    gate = evaluate_gate(session, candidate)
    outcome = gate
    quality_status, quality_reasons = evaluate_quality(candidate)
    if gate == "VERIFIED" and quality_status != "PASS":
        # A verified significant operational change is eligible for the local
        # editorial model.  Generic work without this journal signal remains
        # out of the GPU queue and activity-only as before.
        candidate.status = "candidate" if journal_backed else "needs_review"
        outcome = "EDITORIAL_ASSESSMENT_PENDING" if journal_backed else "NEEDS_REVIEW"
    if (
        gate == "VERIFIED"
        and quality_status == "PASS"
        and auto_publish
        and settings.knowledge_auto_publish
    ):
        case, outcome = publish_candidate(session, candidate)
        if case is not None:
            materialize_case(
                session,
                case,
                vault_dir=settings.vault_dir,
                pipeline_version=settings.pipeline_version,
                content_language=content_language,
            )

    metadata["knowledge_pipeline"] = {
        "state": candidate.status,
        "candidate_id": str(candidate.id),
        "outcome": outcome,
        "auto_publish_eligible": auto_publish,
        "quality_gate_status": quality_status,
        "quality_gate_reasons": quality_reasons,
        "approval_policy": "human_review",
        "knowledge_value": value_assessment,
    }
    stop.metadata_json = metadata
    return candidate, outcome


def reopen_historical_journal_candidates(session: Session) -> dict[str, int | str]:
    """Requeue only old, verified operational journals for editorial review.

    The prior value harness correctly removed generic completion spam, but it
    also prevented the local evidence-bound editor from seeing important
    configuration and recovery changes.  This is a one-time, reversible
    metadata reassessment; immutable activities, evidence, and old candidate
    IDs remain untouched.
    """

    existing = session.get(SystemSetting, _JOURNAL_CANDIDATE_REASSESSMENT_KEY)
    if existing is not None:
        return {
            "state": "already_completed",
            "reopened": int((existing.value or {}).get("reopened") or 0),
            "version": _JOURNAL_CANDIDATE_REASSESSMENT_VERSION,
        }

    journals = {
        str(row.source_stop_activity_id): row
        for row in session.scalars(
            select(ProjectJournalEntry).where(
                ProjectJournalEntry.verification_status == "VERIFIED"
            )
        )
    }
    reopened = 0
    skipped = 0
    now = datetime.now(timezone.utc)
    for candidate in session.scalars(
        select(KnowledgeCandidate).where(KnowledgeCandidate.status == "activity_only")
    ):
        metadata = dict(candidate.metadata_json or {})
        stop_id = str(metadata.get("source_stop_activity_id") or "")
        journal = journals.get(stop_id)
        reasons = list(journal.significance_reasons or []) if journal else []
        eligible_journal = bool(
            journal
            and (
                "verified_failure_and_recovery" in reasons
                or "operational_or_configuration_change" in reasons
            )
        )
        if not (
            eligible_journal
            and candidate.evidence_gate_status == "VERIFIED"
            and metadata.get("auto_generated") is True
            and metadata.get("extractor") == "deterministic-activity-v1"
        ):
            skipped += 1
            continue
        candidate.status = "verified"
        candidate.updated_at = now
        candidate.metadata_json = {
            **metadata,
            "journal_backed": True,
            "journal_significance_reasons": reasons,
            "journal_reassessment": {
                "version": _JOURNAL_CANDIDATE_REASSESSMENT_VERSION,
                "reopened_at": now.isoformat(),
                "reason": "verified_material_project_journal_requires_editorial_assessment",
            },
            "quality_gate_status": "PENDING_EDITORIAL",
            "quality_gate_reasons": [
                "journal_backed_candidate_reopened_for_local_editor"
            ],
        }
        reopened += 1

    session.add(
        SystemSetting(
            key=_JOURNAL_CANDIDATE_REASSESSMENT_KEY,
            value={
                "version": _JOURNAL_CANDIDATE_REASSESSMENT_VERSION,
                "completed_at": now.isoformat(),
                "reopened": reopened,
                "skipped": skipped,
                "reversible": True,
            },
        )
    )
    session.flush()
    return {
        "state": "completed",
        "reopened": reopened,
        "skipped": skipped,
        "version": _JOURNAL_CANDIDATE_REASSESSMENT_VERSION,
    }


def reopen_historical_semantic_candidates(
    session: Session,
    settings: Settings,
) -> dict[str, int | str]:
    """Reassess meaning-rich reports hidden by the old exact-label parser.

    Activities and evidence remain immutable. Existing non-published candidates
    are merely requeued, while previously dropped stops are evaluated by the
    current deterministic gates.
    """

    existing = session.get(SystemSetting, _SEMANTIC_CANDIDATE_REASSESSMENT_KEY)
    if existing is not None:
        return {
            "state": "already_completed",
            "reopened": int((existing.value or {}).get("reopened") or 0),
            "created": int((existing.value or {}).get("created") or 0),
            "version": _SEMANTIC_CANDIDATE_REASSESSMENT_VERSION,
        }

    now = datetime.now(timezone.utc)
    reopened = 0
    created = 0
    skipped = 0
    existing_stop_ids: set[str] = set()
    for candidate in session.scalars(select(KnowledgeCandidate)):
        metadata = dict(candidate.metadata_json or {})
        stop_id = str(metadata.get("source_stop_activity_id") or "")
        if stop_id:
            existing_stop_ids.add(stop_id)
        if (
            candidate.status in {"activity_only", "needs_review"}
            and candidate.evidence_gate_status == "VERIFIED"
            and metadata.get("auto_generated") is True
            and candidate.reported_result.strip()
            and metadata.get("journal_backed") is True
        ):
            candidate.status = "candidate"
            candidate.updated_at = now
            candidate.metadata_json = {
                **metadata,
                "extractor": "deterministic-activity-v2",
                "reported_sources": metadata.get("reported_sources")
                or [
                    {
                        "kind": "final_report",
                        "text": candidate.reported_result[:16000],
                        "verification": "REPORTED_NOT_VERIFIED",
                    }
                ],
                "reported_provenance_reassessment": {
                    "version": _SEMANTIC_CANDIDATE_REASSESSMENT_VERSION,
                    "reopened_at": now.isoformat(),
                    "reason": "natural_language_semantics_were_not_available_to_editor",
                },
            }
            reopened += 1

    stops = list(
        session.scalars(
            select(ActivityEvent).where(
                ActivityEvent.event_type.in_(["Stop", "SubagentStop"]),
                ActivityEvent.metadata_json["knowledge_pipeline"]["reason"].astext
                == "reusable_explanation_missing",
            )
        )
    )
    for stop in stops:
        if str(stop.id) in existing_stop_ids:
            skipped += 1
            continue
        summary = summarize_turn(session, stop)
        if not _journal_significance(summary):
            skipped += 1
            continue
        metadata = dict(stop.metadata_json or {})
        metadata.pop("knowledge_pipeline", None)
        stop.metadata_json = metadata
        candidate, _outcome = finalize_stop(session, stop, settings)
        if candidate is None:
            skipped += 1
        else:
            created += 1

    session.add(
        SystemSetting(
            key=_SEMANTIC_CANDIDATE_REASSESSMENT_KEY,
            value={
                "version": _SEMANTIC_CANDIDATE_REASSESSMENT_VERSION,
                "completed_at": now.isoformat(),
                "reopened": reopened,
                "created": created,
                "skipped": skipped,
                "activities_mutated": False,
                "evidence_mutated": False,
                "reversible": True,
            },
        )
    )
    session.flush()
    return {
        "state": "completed",
        "reopened": reopened,
        "created": created,
        "skipped": skipped,
        "version": _SEMANTIC_CANDIDATE_REASSESSMENT_VERSION,
    }


def reopen_historical_project_journals(session: Session) -> dict[str, int | str]:
    """Reassess project changes that the former knowledge-case gate discarded.

    Project history and reusable knowledge answer different questions. A
    successful file mutation is sufficient to record an observed development
    change, while publishing a reusable case still requires independent
    execution evidence. This one-time metadata reopen preserves all activities
    and creates no knowledge case by itself.
    """

    existing = session.get(SystemSetting, _PROJECT_JOURNAL_REASSESSMENT_KEY)
    if existing is not None:
        return {
            "state": "already_completed",
            "reopened": int((existing.value or {}).get("reopened") or 0),
            "version": _PROJECT_JOURNAL_REASSESSMENT_VERSION,
        }

    journal_stop_ids = set(
        session.scalars(select(ProjectJournalEntry.source_stop_activity_id))
    )
    reopened = 0
    skipped = 0
    now = datetime.now(timezone.utc)
    stops = list(
        session.scalars(
            select(ActivityEvent).where(
                ActivityEvent.event_type.in_(["Stop", "SubagentStop"]),
                ActivityEvent.metadata_json["project_journal"]["state"].astext
                == "activity_only",
            )
        )
    )
    for stop in stops:
        if stop.id in journal_stop_ids:
            skipped += 1
            continue
        if not _journal_significance(summarize_turn(session, stop)):
            skipped += 1
            continue
        metadata = dict(stop.metadata_json or {})
        metadata.pop("project_journal", None)
        stop.metadata_json = metadata
        reopened += 1

    session.add(
        SystemSetting(
            key=_PROJECT_JOURNAL_REASSESSMENT_KEY,
            value={
                "version": _PROJECT_JOURNAL_REASSESSMENT_VERSION,
                "completed_at": now.isoformat(),
                "reopened": reopened,
                "skipped": skipped,
                "activities_deleted": False,
                "knowledge_cases_created": False,
                "reversible": True,
            },
        )
    )
    session.flush()
    return {
        "state": "completed",
        "reopened": reopened,
        "skipped": skipped,
        "version": _PROJECT_JOURNAL_REASSESSMENT_VERSION,
    }


def reassess_project_journal_scope(session: Session) -> dict[str, int | str]:
    """Correct legacy folder-name attribution without deleting journal history."""

    existing = session.get(SystemSetting, _PROJECT_JOURNAL_SCOPE_REASSESSMENT_KEY)
    if existing is not None:
        return {
            "state": "already_completed",
            "reassigned": int((existing.value or {}).get("reassigned") or 0),
            "excluded": int((existing.value or {}).get("excluded") or 0),
            "version": _PROJECT_JOURNAL_SCOPE_REASSESSMENT_VERSION,
        }

    reassigned = 0
    excluded = 0
    unchanged = 0
    now = datetime.now(timezone.utc)
    for journal in session.scalars(select(ProjectJournalEntry)):
        stop = session.get(ActivityEvent, journal.source_stop_activity_id)
        if stop is None:
            unchanged += 1
            continue
        summary = summarize_turn(session, stop)
        metadata = dict(journal.metadata_json or {})
        if not summary.project_scope_verified:
            metadata["scope_reassessment"] = {
                "version": _PROJECT_JOURNAL_SCOPE_REASSESSMENT_VERSION,
                "previous_project_key": journal.project_key,
                "previous_verification_status": journal.verification_status,
                "reason": "no_canonical_repository_path_in_session_or_changed_files",
                "reassessed_at": now.isoformat(),
            }
            journal.verification_status = "OUT_OF_PROJECT_SCOPE"
            journal.metadata_json = metadata
            excluded += 1
            continue
        if journal.project_key == summary.project:
            unchanged += 1
            continue
        previous_project = journal.project_key
        if journal.title.startswith(f"[{previous_project}]"):
            journal.title = (
                f"[{summary.project}]"
                + journal.title[len(f"[{previous_project}]") :]
            )
        journal.project_key = summary.project
        metadata["scope_reassessment"] = {
            "version": _PROJECT_JOURNAL_SCOPE_REASSESSMENT_VERSION,
            "previous_project_key": previous_project,
            "reason": "canonical_changed_path_or_session_anchor",
            "reassessed_at": now.isoformat(),
        }
        journal.metadata_json = metadata
        reassigned += 1

    session.add(
        SystemSetting(
            key=_PROJECT_JOURNAL_SCOPE_REASSESSMENT_KEY,
            value={
                "version": _PROJECT_JOURNAL_SCOPE_REASSESSMENT_VERSION,
                "completed_at": now.isoformat(),
                "reassigned": reassigned,
                "excluded": excluded,
                "unchanged": unchanged,
                "journals_deleted": False,
                "reversible": True,
            },
        )
    )
    session.flush()
    return {
        "state": "completed",
        "reassigned": reassigned,
        "excluded": excluded,
        "unchanged": unchanged,
        "version": _PROJECT_JOURNAL_SCOPE_REASSESSMENT_VERSION,
    }


def finalize_pending_stops(session: Session, settings: Settings) -> dict[str, int]:
    counts = {
        "considered": 0,
        "activity_only": 0,
        "candidates": 0,
        "published": 0,
        "needs_review": 0,
        "journal_recorded": 0,
        "journal_activity_only": 0,
        "journal_materialized": 0,
    }
    journal_projects: set[str] = set()
    journal_reassessment = reopen_historical_project_journals(session)
    counts["journal_reassessment_reopened"] = int(
        journal_reassessment.get("reopened") or 0
    )
    stops = list(
        session.scalars(
            select(ActivityEvent)
            .where(
                ActivityEvent.event_type.in_(["Stop", "SubagentStop"]),
                (
                    ActivityEvent.metadata_json["knowledge_pipeline"].astext.is_(None)
                    | ActivityEvent.metadata_json["project_journal"].astext.is_(None)
                ),
            )
            .order_by(ActivityEvent.occurred_at)
        )
    )
    for stop in stops:
        pipeline_state = (stop.metadata_json or {}).get("knowledge_pipeline")
        counts["considered"] += 1
        summary = summarize_turn(session, stop)
        journal = finalize_project_journal(session, stop, summary, settings)
        if journal is None:
            counts["journal_activity_only"] += 1
        else:
            counts["journal_recorded"] += 1
            journal_projects.add(journal.project_key)
        if pipeline_state:
            continue
        candidate, outcome = finalize_stop(session, stop, settings)
        if candidate is None:
            counts["activity_only"] += 1
        else:
            counts["candidates"] += 1
            if candidate.status == "published":
                counts["published"] += 1
            elif candidate.status in {"candidate", "needs_review", "verified"}:
                counts["needs_review"] += 1
    scope_reassessment = reassess_project_journal_scope(session)
    counts["journal_scope_reassigned"] = int(
        scope_reassessment.get("reassigned") or 0
    )
    counts["journal_scope_excluded"] = int(
        scope_reassessment.get("excluded") or 0
    )
    for project in sorted(journal_projects):
        path = materialize_project_journal(
            session,
            project=project,
            vault_dir=settings.vault_dir,
            pipeline_version=settings.pipeline_version,
            content_language=settings.knowledge_content_language,
        )
        counts["journal_materialized"] += int(path is not None)
    reassessment = reopen_historical_journal_candidates(session)
    counts["journal_candidates_reopened"] = int(reassessment.get("reopened") or 0)
    semantic_reassessment = reopen_historical_semantic_candidates(session, settings)
    counts["semantic_candidates_reopened"] = int(
        semantic_reassessment.get("reopened") or 0
    )
    counts["semantic_candidates_created"] = int(
        semantic_reassessment.get("created") or 0
    )
    return counts
