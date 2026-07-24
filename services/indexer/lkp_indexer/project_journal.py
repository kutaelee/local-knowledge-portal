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
from lkp.settings import get_settings
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from .paths import idempotency_key
from .queue import enqueue

_MAX_ROLLING_ENTRIES = 200


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", value.strip(), flags=re.UNICODE).strip("-_.")
    return cleaned[:100] or "unknown-project"


def _one_line(value: str | None) -> str:
    return " ".join((value or "").split())


def _source_hash(entries: list[ProjectJournalEntry], pipeline_version: str) -> str:
    payload = [
        {
            "id": str(item.id),
            "updated_at": item.updated_at.isoformat(),
            "status": item.verification_status,
            "title": item.title,
            "references": item.knowledge_references_json,
        }
        for item in entries
    ]
    return hashlib.sha256(
        json.dumps(
            {"pipeline": pipeline_version, "entries": payload},
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
) -> str:
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
                f"### {'주요 변경' if korean else 'Changes'}",
                "",
                item.change_summary,
                "",
            ]
        )
        if item.failures_json:
            lines.extend([f"### {'실패와 해결' if korean else 'Failures and resolution'}", ""])
            for failure in item.failures_json:
                lines.append(
                    f"- {_one_line(str(failure.get('command_family') or 'command'))}: "
                    f"exit `{failure.get('exit_code')}`"
                )
            lines.extend(["", item.resolution, ""])
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
    korean = content_language == "ko"
    lines = [
        "---",
        "managed: true",
        "generator: local-knowledge-portal",
        f"project: {json.dumps(project, ensure_ascii=False)}",
        f"source_ids: {json.dumps([f'project-journal:{item.id}' for item in entries])}",
        f"source_hashes: {json.dumps([source_hash])}",
        f"pipeline_version: {json.dumps(pipeline_version)}",
        f"generated_at: {json.dumps(generated_at.isoformat())}",
        'page_type: "project-development-journal-index"',
        'lifecycle_status: "current"',
        "---",
        "",
        f"# {project} 개발 일지" if korean else f"# {project} development journal",
        "",
        (
            "> 최신 주요 변경을 찾기 위한 갱신형 색인입니다. 각 항목의 본문과 실행 "
            "근거는 아래 불변 개별 문서에 보존됩니다."
            if korean
            else (
                "> Current index of significant changes. Full evidence remains in "
                "immutable entries."
            )
        ),
        "",
    ]
    for item in entries:
        entry_name = (
            f"{item.occurred_at.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}-"
            f"{str(item.id)[:8]}"
        )
        summary = _one_line(item.change_summary)[:240]
        lines.extend(
            [
                f"- [[Journal/{entry_name}|{_one_line(item.title)}]]",
                f"  - {item.occurred_at.astimezone(timezone.utc).isoformat()} · "
                f"`{item.verification_status}`",
                f"  - {summary}",
            ]
        )
    lines.append("")
    return "\n".join(lines)


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
        f"entry-v1:{_source_hash([entry], pipeline_version)}".encode()
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
        f"index-v2:{_source_hash(entries, pipeline_version)}".encode()
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
