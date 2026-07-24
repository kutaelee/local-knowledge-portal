from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from lkp.db import SessionLocal
from lkp.models import (
    Document,
    DocumentState,
    EvidenceRecord,
    GeneratedPage,
    KnowledgeCandidate,
    KnowledgeCase,
    KnowledgeOccurrence,
)
from lkp.settings import get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from .knowledge import dedup_key, evaluate_quality

_GENERIC_CAUSE_PREFIXES = (
    "Observed implementation in ",
    "관측된 파일 변경만 확인됐으며",
)


def _is_low_quality_auto_case(case: KnowledgeCase, candidates: list[KnowledgeCandidate]) -> bool:
    if not candidates or not case.root_cause.startswith(_GENERIC_CAUSE_PREFIXES):
        return False
    return all(
        (candidate.metadata_json or {}).get("auto_generated") is True
        and (candidate.metadata_json or {}).get("extractor") == "deterministic-activity-v1"
        for candidate in candidates
    )


def review_low_quality_auto_cases(
    session: Session,
    *,
    vault_dir: Path,
    apply: bool = False,
    content_language: str = "ko",
) -> list[dict[str, str | int | bool]]:
    """Retract generic auto-published cases without deleting their audit trail."""

    results: list[dict[str, str | int | bool]] = []
    cases = list(session.scalars(select(KnowledgeCase).where(KnowledgeCase.status == "verified")))
    for case in cases:
        candidate_ids = list(
            session.scalars(
                select(KnowledgeOccurrence.candidate_id).where(
                    KnowledgeOccurrence.case_id == case.id
                )
            )
        )
        candidates = (
            list(
                session.scalars(
                    select(KnowledgeCandidate).where(KnowledgeCandidate.id.in_(candidate_ids))
                )
            )
            if candidate_ids
            else []
        )
        if not _is_low_quality_auto_case(case, candidates):
            continue

        relative_path = str((case.metadata_json or {}).get("materialized_path") or "")
        result: dict[str, str | int | bool] = {
            "case_id": str(case.id),
            "title": case.title,
            "candidate_count": len(candidates),
            "materialized_path": relative_path,
            "applied": apply,
        }
        results.append(result)
        if not apply:
            continue

        now = datetime.now(timezone.utc)
        case.status = "retired"
        case.metadata_json = {
            **(case.metadata_json or {}),
            "retired_at": now.isoformat(),
            "retired_by": "knowledge-quality-gate-v2",
            "retired_reason": "insufficient_reusable_knowledge",
            "retirement_reversible": True,
        }
        for candidate in candidates:
            candidate.status = "needs_review"
            candidate.updated_at = now
            candidate.metadata_json = {
                **(candidate.metadata_json or {}),
                "quality_gate_status": "NEEDS_REVIEW",
                "quality_gate_reasons": [
                    "generic_file_change_is_not_a_cause",
                    "artifact_list_is_not_a_reusable_solution",
                    "human_restructuring_required",
                ],
                "approval_policy": "human_review",
                "retracted_case_id": str(case.id),
                "retracted_at": now.isoformat(),
            }

        if not relative_path:
            continue
        documents = list(
            session.scalars(select(Document).where(Document.relative_path == relative_path))
        )
        for document in documents:
            document.state = DocumentState.ignored

        source = (vault_dir / relative_path).resolve(strict=False)
        managed_root = (vault_dir / "_generated").resolve(strict=False)
        try:
            source.relative_to(managed_root)
        except ValueError:
            result["quarantine"] = "refused_path_escape"
            continue
        if not source.exists():
            result["quarantine"] = "source_missing"
            continue
        header = source.read_text(encoding="utf-8", errors="strict")[:4096]
        if "managed: true" not in header or "generator: local-knowledge-portal" not in header:
            result["quarantine"] = "refused_non_managed_page"
            continue

        retired_relative = Path("_generated") / "_retired" / "Knowledge-Cases" / source.name
        target = (vault_dir / retired_relative).resolve(strict=False)
        target.relative_to(managed_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            result["quarantine"] = "target_exists"
            continue
        os.replace(source, target)
        page = session.scalar(
            select(GeneratedPage).where(GeneratedPage.relative_path == relative_path)
        )
        if page is not None:
            page.relative_path = retired_relative.as_posix()
        result["quarantine"] = retired_relative.as_posix()
    if apply:
        reviewed_candidates = 0
        auto_candidates = list(session.scalars(select(KnowledgeCandidate)))
        for candidate in auto_candidates:
            metadata = candidate.metadata_json or {}
            if metadata.get("auto_generated") is not True or candidate.status == "published":
                continue
            quality_status, _ = evaluate_quality(candidate)
            if quality_status == "NEEDS_REVIEW":
                candidate.status = "needs_review"
                reviewed_candidates += 1
            if content_language == "ko" and candidate.root_cause.startswith(
                "Observed implementation in "
            ):
                candidate_metadata = dict(candidate.metadata_json or {})
                candidate_metadata.setdefault(
                    "original_extracted_fields",
                    {
                        "root_cause": candidate.root_cause,
                        "solution": candidate.solution,
                    },
                )
                project = str(candidate_metadata.get("project") or "해당 프로젝트")
                candidate.root_cause = (
                    f"{project}에서 파일 변경과 성공한 도구 종료는 확인됐지만, "
                    "재사용 가능한 원인이나 구현 결정은 아직 구조화되지 않았습니다."
                )
                candidate.solution = (
                    "변경 파일 목록과 검증 명령은 실행 근거로 보존했습니다. "
                    "목표·원인 또는 방식·해결 조치·검증 결과를 사용자가 검토해 "
                    "정리하기 전에는 정식 사례로 발행하지 않습니다."
                )
                candidate.dedup_key = dedup_key(
                    candidate.category,
                    candidate.problem,
                    candidate.root_cause,
                    candidate.solution,
                )
                candidate_metadata["content_language"] = "ko"
                candidate.metadata_json = candidate_metadata
                evidence_rows = list(
                    session.scalars(
                        select(EvidenceRecord).where(EvidenceRecord.candidate_id == candidate.id)
                    )
                )
                for evidence in evidence_rows:
                    if evidence.claim.startswith("Observed successful file mutation in "):
                        evidence.claim = f"{project}에서 성공한 파일 변경을 관측했습니다."
                    elif evidence.claim.startswith("Observed successful "):
                        family = evidence.claim.removeprefix("Observed successful ")
                        evidence.claim = f"성공한 {family} 실행을 관측했습니다."
                    elif evidence.claim.startswith("Observed ") and evidence.claim.endswith(
                        " failure"
                    ):
                        family = evidence.claim.removeprefix("Observed ").removesuffix(" failure")
                        evidence.claim = f"{family} 실패를 관측했습니다."
                    if (evidence.verified_value or "").startswith("observed exit_code="):
                        evidence.verified_value = evidence.verified_value.replace(
                            "observed exit_code=", "관측된 exit_code=", 1
                        )
        retired_documents = list(
            session.scalars(
                select(Document).where(
                    Document.relative_path.like("_generated/_retired/Knowledge-Cases/%")
                )
            )
        )
        for document in retired_documents:
            document.state = DocumentState.ignored
        if reviewed_candidates:
            results.append(
                {
                    "kind": "candidate_quality_sweep",
                    "candidate_count": reviewed_candidates,
                    "applied": True,
                }
            )
    session.flush()
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review and retract generic auto-published knowledge cases."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply reversible retirement. Without this flag the command is a dry run.",
    )
    args = parser.parse_args()
    settings = get_settings()
    with SessionLocal() as session:
        results = review_low_quality_auto_cases(
            session,
            vault_dir=settings.vault_dir,
            apply=args.apply,
            content_language=settings.knowledge_content_language,
        )
        if args.apply:
            session.commit()
        else:
            session.rollback()
    print(
        json.dumps(
            {"mode": "apply" if args.apply else "dry-run", "items": results},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
