from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lkp.db import SessionLocal
from lkp.models import GeneratedPage, ProjectJournalEntry, SourceRoot
from lkp.redaction import redact_text
from lkp.settings import get_settings
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from .paths import idempotency_key
from .queue import enqueue

_MAX_ROLLING_ENTRIES = 200
# This is intentionally independent of the indexing pipeline version.  A
# generated page must be rewritten when its presentation semantics change,
# otherwise the managed vault can keep an obsolete layout even though its
# underlying journal record has not changed.
_PRESENTATION_REVISION = "v6-current-project-document"
_PROJECT_PATH = re.compile(
    r"(?ix)(?:[a-z]:/dev/repos|/home/[^/]+/src|//wsl\.localhost/[^/]+/home/[^/]+/src)/"
    r"(?P<project>[^/\\]+)"
)
_GENERIC_COMPLETION = re.compile(
    r"(?ix)^\s*(?:"
    r"완료(?:했습니다|되었습니다|됨)?|검증\s*완료|구현\s*완료|수정\s*완료|"
    r"복구\s*완료|처리\s*완료|done|completed?|fixed|verified|continue"
    r")[.!…\s]*$"
)
_AMBIENT_CONTEXT = re.compile(
    r"(?is)<(?:in-app-browser-context|environment_context|permissions\s+instructions|"
    r"apps_instructions|plugins_instructions|skills_instructions|recommended_plugins)\b[^>]*>.*?"
    r"</(?:in-app-browser-context|environment_context|permissions\s+instructions|"
    r"apps_instructions|plugins_instructions|skills_instructions|recommended_plugins)>"
)
_REQUEST_MARKER = re.compile(r"(?is)^\s*##\s*My request for Codex:\s*")
_LABELED_INTENT = re.compile(r"(?im)^\s*(?:[-*]\s*)?(?:목표|문제|goal|problem)\s*[:：]\s*(.+?)\s*$")


def clean_journal_intent(value: str | None, *, korean: bool = True) -> str:
    """Remove UI-injected context without changing the original activity record.

    Journal rows are a derived presentation. Codex Desktop can prepend browser
    and environment context to a user message; displaying that context as an
    "intent" makes a project history misleading and hard to read.
    """

    cleaned = _AMBIENT_CONTEXT.sub("", value or "").strip()
    cleaned = _REQUEST_MARKER.sub("", cleaned).strip()
    if not cleaned:
        return "명시된 사용자 작업 목표 없음" if korean else "No explicit user goal"
    return redact_text(cleaned[:8000])


def clean_journal_summary(value: str | None) -> str:
    """Drop an acknowledgement-only lead while preserving reported details."""

    lines = (value or "").strip().splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    if len(lines) > 1 and _GENERIC_COMPLETION.match(lines[0]):
        lines.pop(0)
    result = "\n".join(lines).strip()
    return redact_text(result or (value or "").strip())


def journal_title(
    project: str,
    *,
    intent: str | None,
    report: str | None,
    changed_files: list[str] | tuple[str, ...],
    failures: bool,
    operational: bool,
    korean: bool = True,
) -> str:
    """Create a stable human title without treating a completion claim as fact."""

    labeled = _LABELED_INTENT.search(report or "")
    first_report = _report_title_line(report)
    first_intent = _one_line(intent)
    # A cleaned work report describes the resulting change, whereas the prompt
    # is often conversational (for example, "yes, do that"). A structured
    # report goal wins, then the report, then the original request as a last
    # resort. This is presentation only; both source fields stay preserved.
    candidate = labeled.group(1) if labeled else first_report or first_intent
    if not candidate or _GENERIC_COMPLETION.match(candidate):
        candidate = first_intent
    if not candidate or _GENERIC_COMPLETION.match(candidate):
        if failures:
            candidate = "오류 수정 및 검증" if korean else "Error remediation and verification"
        elif operational:
            candidate = "운영·설정 변경 및 검증" if korean else "Operational/configuration change"
        elif changed_files:
            candidate = "검증된 구현 변경" if korean else "Verified implementation change"
        else:
            candidate = "검증된 프로젝트 변경" if korean else "Verified project change"
    return f"[{project}] {candidate[:140]}"


def _project_scoped_files(
    paths: tuple[str, ...], project: str
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Separate repository files from related global operational files.

    The activity ledger is authoritative and remains unchanged. This helper
    only improves the derived project-journal presentation, including legacy
    entries created before per-project scope was recorded.
    """

    scoped = tuple(
        path
        for path in paths
        if (match := _PROJECT_PATH.search(path.replace("\\", "/")))
        and match.group("project") == project
    )
    if not scoped:
        return paths, ()
    return scoped, tuple(path for path in paths if path not in scoped)


def refresh_journal_presentation(
    session: Session,
    *,
    vault_dir: Path,
    pipeline_version: str,
    content_language: str,
) -> dict[str, int]:
    """Upgrade derived journal wording while retaining prior text in metadata.

    This is intentionally an additive, idempotent presentation refresh: source
    activity events are never changed and generated pages stay inside the
    managed vault directory.
    """

    korean = content_language == "ko"
    changed = 0
    projects: set[str] = set()
    for entry in session.scalars(select(ProjectJournalEntry)):
        metadata = dict(entry.metadata_json or {})
        previous = dict(metadata.get("journal_presentation_v1") or {})
        raw_intent = entry.intent
        raw_summary = entry.change_summary
        raw_files = tuple(entry.changed_files or [])
        cleaned_intent = clean_journal_intent(raw_intent, korean=korean)
        cleaned_summary = clean_journal_summary(raw_summary)
        scoped_files, related_operational_files = _project_scoped_files(
            raw_files, entry.project_key
        )
        expected_title = journal_title(
            entry.project_key,
            intent=cleaned_intent,
            report=cleaned_summary,
            changed_files=list(scoped_files),
            failures=bool(entry.failures_json),
            operational="operational_or_configuration_change" in (entry.significance_reasons or []),
            korean=korean,
        )
        if (
            entry.title == expected_title
            and entry.intent == cleaned_intent
            and entry.change_summary == cleaned_summary
            and entry.changed_files == list(scoped_files)
            and metadata.get("journal_presentation_version") == _PRESENTATION_REVISION
            and metadata.get("related_operational_files", [])
            == list(related_operational_files)
        ):
            continue
        previous.setdefault("title", entry.title)
        previous.setdefault("intent", raw_intent)
        previous.setdefault("change_summary", raw_summary)
        previous.setdefault("changed_files", list(raw_files))
        metadata["journal_presentation_v1"] = previous
        metadata["journal_presentation_version"] = _PRESENTATION_REVISION
        metadata["raw_activity_preserved"] = True
        entry.title = expected_title
        entry.intent = cleaned_intent
        entry.change_summary = cleaned_summary
        entry.changed_files = list(scoped_files)
        metadata["related_operational_files"] = list(related_operational_files[:200])
        metadata["project_scope"] = (
            "repository_paths_only" if related_operational_files else "all_paths"
        )
        entry.metadata_json = metadata
        changed += 1
        projects.add(entry.project_key)
    session.flush()
    for project in projects:
        materialize_project_journal(
            session,
            project=project,
            vault_dir=vault_dir,
            pipeline_version=pipeline_version,
            content_language=content_language,
        )
    return {"changed": changed, "projects": len(projects)}


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", value.strip(), flags=re.UNICODE).strip("-_.")
    return cleaned[:100] or "unknown-project"


def _one_line(value: str | None) -> str:
    return " ".join((value or "").split())


def journal_category(entry: ProjectJournalEntry) -> str:
    metadata = getattr(entry, "metadata_json", {}) or {}
    configured = metadata.get("work_type")
    if configured:
        return str(configured)
    if getattr(entry, "failures_json", None):
        return "error_resolution"
    searchable = (
        f"{getattr(entry, 'title', '')} {getattr(entry, 'change_summary', '')}"
    ).casefold()
    if any(
        token in searchable
        for token in ("cpu", "latency", "performance", "load", "성능", "부하", "지연")
    ):
        return "performance"
    if "operational_or_configuration_change" in (
        getattr(entry, "significance_reasons", []) or []
    ):
        return "operations"
    return "implementation"


def current_journal_sources(
    entries: list[ProjectJournalEntry],
) -> list[tuple[str, ProjectJournalEntry]]:
    """Select the newest source for each current-state topic.

    A project page is a current snapshot, not a concatenated activity feed.
    Older records remain available through the immutable Journal history.
    """

    selected: dict[str, ProjectJournalEntry] = {}
    for entry in sorted(
        entries,
        key=lambda item: (item.occurred_at, str(item.id)),
        reverse=True,
    ):
        selected.setdefault(journal_category(entry), entry)
    return sorted(
        selected.items(),
        key=lambda item: (item[1].occurred_at, str(item[1].id)),
        reverse=True,
    )


def _current_section_title(category: str, *, korean: bool) -> str:
    labels = {
        "implementation": ("현재 구현", "Current implementation"),
        "operations": ("운영과 설정", "Operations and configuration"),
        "performance": ("성능과 안정성", "Performance and reliability"),
        "error_resolution": ("오류 해결", "Resolved issues"),
    }
    label = labels.get(category, ("현재 상태", "Current state"))
    return label[0] if korean else label[1]


def _render_current_project_document(
    project: str,
    entries: list[ProjectJournalEntry],
    *,
    source_hash: str,
    pipeline_version: str,
    generated_at: datetime,
    content_language: str,
) -> str:
    korean = content_language == "ko"
    sources = current_journal_sources(entries)
    source_ids = [f"project-journal:{item.id}" for _, item in sources]
    lines = [
        "---",
        "managed: true",
        "generator: local-knowledge-portal",
        f"project: {json.dumps(project, ensure_ascii=False)}",
        f"source_ids: {json.dumps(source_ids)}",
        f"source_hashes: {json.dumps([source_hash])}",
        f"pipeline_version: {json.dumps(pipeline_version)}",
        f"generated_at: {json.dumps(generated_at.isoformat())}",
        'page_type: "project-current-document"',
        'lifecycle_status: "current"',
        "---",
        "",
        f"# {project} 프로젝트 문서" if korean else f"# {project} project document",
        "",
        (
            "> 프로젝트의 최신 상태만 주제별로 유지합니다. 번호는 본문을 만든 "
            "원본 작업 이력과 실행 근거로 연결됩니다."
            if korean
            else (
                "> Current project state by topic. Citation numbers link to the "
                "source work record and its execution evidence."
            )
        ),
        "",
        f"- {'최근 갱신' if korean else 'Last updated'}: `{generated_at.isoformat()}`",
        f"- {'전체 작업 이력' if korean else 'History entries'}: `{len(entries)}`",
        "",
    ]
    for citation_number, (category, item) in enumerate(sources, start=1):
        status = (
            "검증됨"
            if korean and item.verification_status == "VERIFIED"
            else item.verification_status
        )
        lines.extend(
            [
                f"## {_current_section_title(category, korean=korean)} [{citation_number}]",
                "",
                f"*{'기준 시각' if korean else 'As of'}: "
                f"{item.occurred_at.astimezone(timezone.utc).isoformat()} · "
                f"{status}*",
                "",
                item.change_summary,
                "",
            ]
        )
    lines.extend(
        [
            f"## {'근거' if korean else 'Sources'}",
            "",
            (
                "본문에 사용한 최신 원문만 표시합니다. 전체 기록은 프로젝트의 "
                "`Journal/` 이력에 보존됩니다."
                if korean
                else (
                    "Only sources used in the current document are listed. Full history "
                    "remains under the project's `Journal/` directory."
                )
            ),
            "",
        ]
    )
    for citation_number, (_, item) in enumerate(sources, start=1):
        entry_name = (
            f"{item.occurred_at.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-"
            f"{str(item.id)[:8]}"
        )
        lines.append(
            f"- [{citation_number}] [[Journal/{entry_name}|"
            f"{'원본 작업 기록' if korean else 'Source work record'}]] · "
            f"{'실행 근거' if korean else 'execution evidence'} "
            f"{len(item.verification_json or [])}"
        )
    lines.append("")
    return "\n".join(lines)


def _report_title_line(value: str | None) -> str:
    """Return a compact reported-work headline without flattening every bullet.

    `change_summary` is markdown. Flattening it made the list card title absorb
    an entire report, while a bare "implemented and pushed" opener carried no
    useful subject. Prefer the first specific prose or bullet instead.
    """

    generic_prefix = re.compile(
        r"^(?:완료(?:했습니다|되었습니다|됨)?|"
        r"(?:구현|수정|검증|푸시|재기동|복구|처리)"
        r"(?:\s*및\s*(?:푸시|재기동|검증|배포|기동))?\s*완료(?:했습니다|되었습니다|됨)?)"
        r"[.!…\s]*",
        re.IGNORECASE,
    )
    for raw_line in (value or "").splitlines():
        line = raw_line.strip().lstrip("-* ").strip()
        if not line or line.startswith("재사용 메모"):
            continue
        line = generic_prefix.sub("", line).strip()
        if line:
            return line
    return ""


def _source_hash(entries: list[ProjectJournalEntry], pipeline_version: str) -> str:
    payload = [
        {
            "id": str(item.id),
            "updated_at": item.updated_at.isoformat(),
            "status": item.verification_status,
            "title": item.title,
            "intent": item.intent,
            "change_summary": item.change_summary,
            "failures": item.failures_json,
            "resolution": item.resolution,
            "changed_files": item.changed_files,
            "references": item.knowledge_references_json,
        }
        for item in entries
    ]
    return hashlib.sha256(
        json.dumps(
            {
                "pipeline": pipeline_version,
                "presentation": _PRESENTATION_REVISION,
                "entries": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def render_project_journal(
    project: str,
    entries: list[ProjectJournalEntry],
    *,
    source_hash: str,
    pipeline_version: str,
    generated_at: datetime,
    content_language: str,
    integrated: bool = True,
) -> str:
    if integrated:
        return _render_current_project_document(
            project,
            entries,
            source_hash=source_hash,
            pipeline_version=pipeline_version,
            generated_at=generated_at,
            content_language=content_language,
        )
    korean = content_language == "ko"
    source_ids = [f"project-journal:{item.id}" for item in entries]
    lines = [
        "---",
        "managed: true",
        "generator: local-knowledge-portal",
        f"project: {json.dumps(project, ensure_ascii=False)}",
        f"source_ids: {json.dumps(source_ids)}",
        f"source_hashes: {json.dumps([source_hash])}",
        f"pipeline_version: {json.dumps(pipeline_version)}",
        f"generated_at: {json.dumps(generated_at.isoformat())}",
        'page_type: "project-development-journal"',
        'lifecycle_status: "current"',
        "---",
        "",
        f"# {project} 개발 일지" if korean else f"# {project} development journal",
        "",
        (
            "> 주요 구현·설정·운영 변경을 실행 근거와 함께 시간순으로 정리합니다. "
            "재사용 가능한 정식 지식 사례와는 별도이며, 작업 보고와 검증 결과를 구분합니다."
            if korean
            else "> Significant implementation, configuration, and operational changes, "
            "kept separately from reusable canonical knowledge cases."
        ),
        "",
    ]
    for item in entries:
        when = item.occurred_at.astimezone(timezone.utc).isoformat()
        status = (
            "검증됨"
            if item.verification_status == "VERIFIED" and korean
            else item.verification_status
        )
        lines.extend(
            [
                f"## {item.title}",
                "",
                f"- {'시각' if korean else 'Time'}: `{when}`",
                f"- {'상태' if korean else 'Status'}: `{status}`",
                f"- {'기록 ID' if korean else 'Entry'}: `{item.id}`",
                "",
                f"### {'의도' if korean else 'Intent'}",
                "",
                item.intent or ("명시된 의도 없음" if korean else "No explicit intent"),
                "",
                (
                    "### 작업자 보고 (검증 전 주장 포함)"
                    if korean
                    else "### Reported work (may include unverified claims)"
                ),
                "",
                item.change_summary,
                "",
            ]
        )
        lines.extend([f"### {'관측된 변경 파일' if korean else 'Observed changed files'}", ""])
        if item.changed_files:
            for changed_file in item.changed_files[:100]:
                lines.append(f"- `{changed_file}`")
        else:
            lines.append(
                "- 관측된 변경 파일 없음" if korean else "- No changed files observed."
            )
        lines.append("")
        lines.extend([f"### {'오류 및 조치' if korean else 'Failures and resolution'}", ""])
        if item.failures_json:
            for failure in item.failures_json:
                lines.append(
                    f"- {_one_line(str(failure.get('command_family') or 'command'))}: "
                    f"exit `{failure.get('exit_code')}`"
                )
            lines.extend(["", item.resolution, ""])
        else:
            lines.extend(
                [
                    "- 관측된 명령 실패 없음. 이 항목은 오류 해결 사례로 주장하지 않습니다."
                    if korean
                    else (
                        "- No failed command was observed. This entry does not claim an "
                        "error-resolution case."
                    ),
                    "",
                ]
            )
        lines.extend([f"### {'검증' if korean else 'Verification'}", ""])
        for evidence in item.verification_json:
            lines.append(
                f"- {_one_line(str(evidence.get('command_family') or 'validation'))}: "
                f"exit `{evidence.get('exit_code')}`"
            )
        if not item.verification_json:
            lines.append("- 검증 실행 근거 없음" if korean else "- No execution evidence")
        lines.extend([f"### {'지식베이스 참조' if korean else 'Knowledge references'}", ""])
        if item.knowledge_references_json:
            for reference in item.knowledge_references_json:
                locator = reference.get("canonical_path") or reference.get("relative_path")
                chunk = reference.get("chunk_id")
                score = reference.get("retrieval_score")
                lines.append(
                    f"- `{reference.get('document_id') or 'document'}`"
                    f"{f' · `{locator}`' if locator else ''}"
                    f"{f' · chunk `{chunk}`' if chunk else ''}"
                    f"{f' · score `{score}`' if score is not None else ''}"
                )
        else:
            lines.append(
                "- 이 작업에서 구조화된 지식베이스 참조가 관측되지 않았습니다."
                if korean
                else "- No structured knowledge-base reference was observed."
            )
        lines.extend(["", "---", ""])
    lines.append("")
    return "\n".join(lines)


def render_project_journal_index(
    project: str,
    entries: list[ProjectJournalEntry],
    *,
    source_hash: str,
    pipeline_version: str,
    generated_at: datetime,
    content_language: str,
) -> str:
    return render_project_journal(
        project,
        entries,
        source_hash=source_hash,
        pipeline_version=pipeline_version,
        generated_at=generated_at,
        content_language=content_language,
    )


def _write_entry_page(
    session: Session,
    entry: ProjectJournalEntry,
    *,
    vault_dir: Path,
    pipeline_version: str,
    content_language: str,
    root: SourceRoot | None,
) -> Path:
    project = entry.project_key
    entry_name = (
        f"{entry.occurred_at.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-"
        f"{str(entry.id)[:8]}"
    )
    relative_path = (
        Path("_generated")
        / "Projects"
        / _slug(project)
        / "Journal"
        / f"{entry_name}.md"
    )
    target = (vault_dir / relative_path).resolve(strict=False)
    managed_root = (vault_dir / "_generated" / "Projects").resolve(strict=False)
    target.relative_to(managed_root)
    relative_posix = relative_path.as_posix()
    source_hash = hashlib.sha256(
        f"entry-v2:{_source_hash([entry], pipeline_version)}".encode()
    ).hexdigest()
    page = session.scalar(
        select(GeneratedPage).where(GeneratedPage.relative_path == relative_posix)
    )
    if page is not None and page.source_hashes == [source_hash] and target.exists():
        return target
    if target.exists():
        header = target.read_text(encoding="utf-8", errors="strict")[:4096]
        if "managed: true" not in header or "generator: local-knowledge-portal" not in header:
            raise PermissionError(f"refusing to overwrite non-managed journal entry: {target}")
    now = datetime.now(timezone.utc)
    content = render_project_journal(
        project,
        [entry],
        source_hash=source_hash,
        pipeline_version=pipeline_version,
        generated_at=now,
        content_language=content_language,
        integrated=False,
    ).replace(
        'page_type: "project-development-journal"',
        'page_type: "project-development-journal-entry"',
        1,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{entry_name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, target)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    if page is None:
        page = GeneratedPage(relative_path=relative_posix)
        session.add(page)
    page.source_hashes = [source_hash]
    page.pipeline_version = pipeline_version
    page.generated_at = now
    if root is not None:
        info = target.stat()
        enqueue(
            session,
            key=idempotency_key(str(root.id), str(target), info.st_size, info.st_mtime_ns),
            source_root_id=root.id,
            canonical_path=str(target),
            job_type="index",
            priority=20,
            details={
                "project": project,
                "page_type": "project_journal_entry",
                "entry_id": str(entry.id),
                "source_hash": source_hash,
            },
            coalesce_pending=True,
        )
    session.flush()
    return target


def materialize_project_journal(
    session: Session,
    *,
    project: str,
    vault_dir: Path,
    pipeline_version: str,
    content_language: str,
) -> Path | None:
    entries = list(
        session.scalars(
            select(ProjectJournalEntry)
            .where(ProjectJournalEntry.project_key == project)
            .order_by(
                ProjectJournalEntry.occurred_at.desc(),
                ProjectJournalEntry.id.desc(),
            )
            .limit(_MAX_ROLLING_ENTRIES)
        )
    )
    if not entries:
        return None
    now = datetime.now(timezone.utc)
    source_hash = hashlib.sha256(
        f"integrated-v4:{_source_hash(entries, pipeline_version)}".encode()
    ).hexdigest()
    root = session.scalar(
        select(SourceRoot).where(
            SourceRoot.canonical_path == str(vault_dir.resolve(strict=False))
        )
    )
    for entry in entries:
        _write_entry_page(
            session,
            entry,
            vault_dir=vault_dir,
            pipeline_version=pipeline_version,
            content_language=content_language,
            root=root,
        )
    relative_path = (
        Path("_generated") / "Projects" / _slug(project) / "development-journal.md"
    )
    managed_root = (vault_dir / "_generated" / "Projects").resolve(strict=False)
    target = (vault_dir / relative_path).resolve(strict=False)
    try:
        target.relative_to(managed_root)
    except ValueError as exc:
        raise ValueError("project journal path escaped the managed directory") from exc
    relative_posix = relative_path.as_posix()
    page = session.scalar(
        select(GeneratedPage).where(GeneratedPage.relative_path == relative_posix)
    )
    if page is not None and page.source_hashes == [source_hash] and target.exists():
        return target
    if target.exists():
        header = target.read_text(encoding="utf-8", errors="strict")[:4096]
        if "managed: true" not in header or "generator: local-knowledge-portal" not in header:
            raise PermissionError(f"refusing to overwrite non-managed journal: {target}")
    content = render_project_journal_index(
        project,
        entries,
        source_hash=source_hash,
        pipeline_version=pipeline_version,
        generated_at=now,
        content_language=content_language,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=".development-journal.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, target)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    if page is None:
        page = GeneratedPage(relative_path=relative_posix)
        session.add(page)
    page.source_hashes = [source_hash]
    page.pipeline_version = pipeline_version
    page.generated_at = now
    if root is not None:
        info = target.stat()
        enqueue(
            session,
            key=idempotency_key(str(root.id), str(target), info.st_size, info.st_mtime_ns),
            source_root_id=root.id,
            canonical_path=str(target),
            job_type="index",
            priority=20,
            details={
                "project": project,
                "page_type": "project_development_journal",
                "source_hash": source_hash,
            },
            coalesce_pending=True,
        )
    session.flush()
    return target


def materialize_all_project_journals(session: Session) -> dict[str, str]:
    settings = get_settings()
    projects = list(
        session.scalars(
            select(distinct(ProjectJournalEntry.project_key)).order_by(
                ProjectJournalEntry.project_key
            )
        )
    )
    results: dict[str, str] = {}
    for project in projects:
        path = materialize_project_journal(
            session,
            project=project,
            vault_dir=settings.vault_dir,
            pipeline_version=settings.pipeline_version,
            content_language=settings.knowledge_content_language,
        )
        if path is not None:
            results[project] = str(path)
    session.commit()
    return results


def main() -> int:
    with SessionLocal() as session:
        results = materialize_all_project_journals(session)
    print(json.dumps(results, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
