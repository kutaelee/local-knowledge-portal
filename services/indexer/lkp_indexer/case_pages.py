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
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .queue import enqueue


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "-", value.strip(), flags=re.UNICODE).strip("-_.")
    return cleaned[:100] or "knowledge-case"


def _one_line(value: str | None) -> str:
    return " ".join((value or "").split())


def _source_hash(case: KnowledgeCase, evidence: list[EvidenceRecord]) -> str:
    payload = {
        "id": str(case.id),
        "category": case.category,
        "title": case.title,
        "problem": case.problem,
        "symptom": case.symptom,
        "root_cause": case.root_cause,
        "solution": case.solution,
        "status": case.status,
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
) -> str:
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
        (
            "> 검증된 canonical 지식 사례입니다. 아래 내용은 현재 revision이며 "
            "이전 내용은 사례 이력에 보존됩니다."
        ),
        "",
        "## 문제 / Problem",
        "",
        case.problem,
        "",
        "## 증상 / Symptom",
        "",
        case.symptom,
        "",
        "## 확인된 원인 / Verified root cause",
        "",
        case.root_cause,
        "",
        "## 현재 조치 / Current resolution",
        "",
        case.solution,
        "",
        "## 검증 근거 / Evidence",
        "",
    ]
    for item in evidence:
        state = "verified" if item.verified else "reported"
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
        lines.append("- 검증 근거가 연결되지 않았습니다. 이 상태로 발행하면 안 됩니다.")
    lines.extend(
        [
            "",
            "## 식별자 / Provenance",
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
    revision_number = session.scalar(
        select(func.max(KnowledgeCaseRevision.revision_number)).where(
            KnowledgeCaseRevision.case_id == case.id
        )
    )
    if not revision_number:
        raise ValueError("refusing to materialize a case without a revision")

    now = datetime.now(timezone.utc)
    source_hash = _source_hash(case, evidence)
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
        enqueue(
            session,
            key=f"knowledge-case:{case.id}:revision:{revision_number}:{source_hash}",
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
