from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from lkp.models import (
    EvidenceRecord,
    GeneratedPage,
    KnowledgeCase,
    KnowledgeCaseRevision,
    KnowledgeOccurrence,
    SourceRoot,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from .paths import idempotency_key
from .queue import enqueue


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", value.strip(), flags=re.UNICODE).strip("-_.")
    return cleaned[:100] or "knowledge-case"


def _one_line(value: str | None) -> str:
    return " ".join((value or "").split())


def _source_hash(
    case: KnowledgeCase,
    evidence: list[EvidenceRecord],
    revision_content: dict,
    content_language: str,
) -> str:
    payload = {
        "id": str(case.id),
        "category": case.category,
        "title": case.title,
        "problem": case.problem,
        "symptom": case.symptom,
        "root_cause": case.root_cause,
        "solution": case.solution,
        "status": case.status,
        "content_language": content_language,
        "revision_content": revision_content,
        "evidence": [
            {
                "type": item.evidence_type,
                "claim": item.claim,
                "locator": item.locator,
                "verified_value": item.verified_value,
                "exit_code": item.exit_code,
                "verified": item.verified,
            }
            for item in evidence
        ],
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def render_case_markdown(
    case: KnowledgeCase,
    *,
    revision_number: int,
    evidence: list[EvidenceRecord],
    source_hash: str,
    pipeline_version: str,
    generated_at: datetime,
    revision_content: dict | None = None,
    content_language: str = "ko",
) -> str:
    revision_content = revision_content or {}
    korean = content_language == "ko"
    if case.category in {"implementation", "custom_success"} and korean:
        problem_heading = "목표"
        symptom_heading = "작업 범위"
        cause_heading = "검증된 구현 방식"
        solution_heading = "적용 조치"
    elif case.category in {"implementation", "custom_success"}:
        problem_heading = "Goal"
        symptom_heading = "Scope"
        cause_heading = "Verified implementation approach"
        solution_heading = "Applied change"
    elif korean:
        problem_heading = "문제"
        symptom_heading = "증상"
        cause_heading = "확인된 원인"
        solution_heading = "현재 조치"
    else:
        problem_heading = "Problem"
        symptom_heading = "Symptom"
        cause_heading = "Verified root cause"
        solution_heading = "Current resolution"
    canonical_notice = (
        "> 검증된 정식 지식 사례입니다. 아래 내용은 현재 개정본이며 "
        "이전 내용은 사례 이력에 보존됩니다."
        if korean
        else "> This is a verified canonical knowledge case. Prior revisions remain in history."
    )
    reported_heading = "보고된 결과" if korean else "Reported outcome"
    reported_notice = (
        "> 이 내용은 작업 종료 보고에서 가져왔으며 그 자체는 검증 근거가 아닙니다. "
        "아래 실행 증거와 구분해서 읽어야 합니다."
        if korean
        else "> This came from the completion report and is not evidence by itself."
    )
    verified_heading = "검증된 결과" if korean else "Verified result"
    evidence_heading = "검증 근거" if korean else "Evidence"
    provenance_heading = "출처" if korean else "Provenance"
    lines = [
        "---",
        "managed: true",
        "generator: local-knowledge-portal",
        f"source_ids: [{json.dumps(f'knowledge-case:{case.id}')} ]",
        f"source_hashes: [{json.dumps(source_hash)}]",
        f"pipeline_version: {json.dumps(pipeline_version)}",
        f"generated_at: {json.dumps(generated_at.isoformat())}",
        'evidence_gate: "VERIFIED"',
        f"category: {json.dumps(case.category)}",
        f"case_id: {json.dumps(str(case.id))}",
        f"case_revision: {revision_number}",
        "---",
        "",
        f"# {case.title}",
        "",
        canonical_notice,
        "",
        f"## {problem_heading}",
        "",
        case.problem,
        "",
        f"## {symptom_heading}",
        "",
        case.symptom,
        "",
        f"## {cause_heading}",
        "",
        case.root_cause,
        "",
        f"## {solution_heading}",
        "",
        case.solution,
        "",
        f"## {reported_heading}",
        "",
        reported_notice,
        "",
        str(
            revision_content.get("reported_result")
            or ("보고된 결과 없음" if korean else "No reported outcome")
        ),
        "",
        f"## {verified_heading}",
        "",
        str(
            revision_content.get("verified_result")
            or ("검증된 결과 없음" if korean else "No verified result")
        ),
        "",
        f"## {evidence_heading}",
        "",
    ]
    for item in evidence:
        state = (
            ("검증됨" if item.verified else "보고됨")
            if korean
            else ("verified" if item.verified else "reported")
        )
        lines.append(
            f"- **{state} · {item.evidence_type}**: {_one_line(item.claim)}"
        )
        if item.locator:
            lines.append(f"  - locator: `{_one_line(item.locator)}`")
        if item.verified_value:
            lines.append(f"  - observed: {_one_line(item.verified_value)}")
        if item.exit_code is not None:
            lines.append(f"  - exit code: `{item.exit_code}`")
    if not evidence:
        lines.append(
            "- 검증 근거가 연결되지 않았습니다. 이 상태로 발행하면 안 됩니다."
            if korean
            else "- No verified evidence is linked. This case must not be published."
        )
    lines.extend(
        [
            "",
            f"## {provenance_heading}",
            "",
            f"- case: `{case.id}`",
            f"- revision: `{revision_number}`",
            f"- content hash: `{source_hash}`",
            "",
        ]
    )
    return "\n".join(lines)


def materialize_case(
    session: Session,
    case: KnowledgeCase,
    *,
    vault_dir: Path,
    pipeline_version: str,
    content_language: str = "ko",
) -> Path:
    candidate_ids = list(
        session.scalars(
            select(KnowledgeOccurrence.candidate_id).where(
                KnowledgeOccurrence.case_id == case.id
            )
        )
    )
    evidence = (
        list(
            session.scalars(
                select(EvidenceRecord)
                .where(EvidenceRecord.candidate_id.in_(candidate_ids))
                .order_by(EvidenceRecord.created_at)
            )
        )
        if candidate_ids
        else []
    )
    if not evidence or not all(item.verified for item in evidence):
        raise ValueError("refusing to materialize a case without verified evidence")
    revision = session.scalar(
        select(KnowledgeCaseRevision)
        .where(KnowledgeCaseRevision.case_id == case.id)
        .order_by(KnowledgeCaseRevision.revision_number.desc())
    )
    if revision is None:
        raise ValueError("refusing to materialize a case without a revision")
    revision_number = revision.revision_number

    now = datetime.now(timezone.utc)
    source_hash = _source_hash(
        case,
        evidence,
        revision.content_json,
        content_language,
    )
    metadata = dict(case.metadata_json or {})
    relative_value = metadata.get("materialized_path")
    if relative_value:
        relative_path = Path(str(relative_value))
    else:
        relative_path = (
            Path("_generated")
            / "Knowledge-Cases"
            / f"{_slug(case.title)}-{str(case.id)[:8]}.md"
        )
    managed_root = (vault_dir / "_generated" / "Knowledge-Cases").resolve(
        strict=False
    )
    target = (vault_dir / relative_path).resolve(strict=False)
    try:
        target.relative_to(managed_root)
    except ValueError as exc:
        raise ValueError("knowledge case path escaped the managed directory") from exc
    if target.exists():
        header = target.read_text(encoding="utf-8", errors="strict")[:4096]
        if "managed: true" not in header or "generator: local-knowledge-portal" not in header:
            raise PermissionError(f"refusing to overwrite non-managed page: {target}")
    relative_posix = relative_path.as_posix()
    page = session.scalar(
        select(GeneratedPage).where(GeneratedPage.relative_path == relative_posix)
    )
    if page is not None and page.source_hashes == [source_hash] and target.exists():
        metadata.update(
            {
                "materialized_path": relative_posix,
                "materialized_hash": source_hash,
                "materialized_revision": revision_number,
                "materialized_at": page.generated_at.isoformat(),
            }
        )
        case.metadata_json = metadata
        session.flush()
        return target

    content = render_case_markdown(
        case,
        revision_number=revision_number,
        evidence=evidence,
        source_hash=source_hash,
        pipeline_version=pipeline_version,
        generated_at=now,
        revision_content=revision.content_json,
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
            prefix=f".{target.name}.",
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

    metadata.update(
        {
            "materialized_path": relative_posix,
            "materialized_hash": source_hash,
            "materialized_revision": revision_number,
            "materialized_at": now.isoformat(),
        }
    )
    case.metadata_json = metadata

    root = session.scalar(
        select(SourceRoot).where(
            SourceRoot.canonical_path == str(vault_dir.resolve(strict=False))
        )
    )
    if root is not None:
        info = target.stat()
        enqueue(
            session,
            key=idempotency_key(
                str(root.id),
                str(target),
                info.st_size,
                info.st_mtime_ns,
            ),
            source_root_id=root.id,
            canonical_path=str(target),
            job_type="index",
            priority=20,
            details={
                "knowledge_case_id": str(case.id),
                "knowledge_case_revision": revision_number,
                "source_hash": source_hash,
            },
        )
    session.flush()
    return target
