from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from lkp.models import (
    ActivityEvent,
    EvidenceRecord,
    KnowledgeCandidate,
    KnowledgeCase,
    KnowledgeCaseRelation,
    KnowledgeCaseRevision,
    KnowledgeOccurrence,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

_EXECUTION_TOOLS = {
    "bash",
    "exec_command",
    "powershell",
    "pwsh",
    "shell_command",
    "terminal",
}
_EXECUTION_EVIDENCE_TYPES = {
    "build_pass",
    "command_failure",
    "command_success",
    "test_pass",
}


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _hash(*values: str) -> str:
    return hashlib.sha256("\x1f".join(_normalize(item) for item in values).encode()).hexdigest()


def is_execution_tool(tool_name: str | None) -> bool:
    normalized = (tool_name or "").rsplit(".", 1)[-1].casefold()
    return normalized in _EXECUTION_TOOLS


def dedup_key(category: str, problem: str, root_cause: str, solution: str) -> str:
    return _hash(category, problem, root_cause, solution)


def similarity_key(symptom: str) -> str:
    return _hash(symptom)


def _jaccard(left: str, right: str) -> float:
    a = set(_normalize(left).split())
    b = set(_normalize(right).split())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def create_candidate(
    session: Session,
    *,
    category: str,
    title: str,
    problem: str,
    symptom: str,
    root_cause: str,
    solution: str,
    reported_result: str | None,
    verified_result: str | None,
    evidence: list[dict[str, Any]],
    metadata: dict[str, Any] | None = None,
) -> KnowledgeCandidate:
    candidate = KnowledgeCandidate(
        category=category,
        title=title,
        problem=problem,
        symptom=symptom,
        root_cause=root_cause,
        solution=solution,
        reported_result=reported_result,
        verified_result=verified_result,
        dedup_key=dedup_key(category, problem, root_cause, solution),
        similarity_key=similarity_key(symptom),
        metadata_json=metadata or {},
    )
    session.add(candidate)
    session.flush()
    for item in evidence:
        session.add(
            EvidenceRecord(
                candidate_id=candidate.id,
                activity_id=item.get("activity_id"),
                evidence_type=item["evidence_type"],
                claim=item["claim"],
                locator=item.get("locator"),
                reported_value=item.get("reported_value"),
                verified_value=item.get("verified_value"),
                exit_code=item.get("exit_code"),
                verified=item.get("verified", False),
                metadata_json=item.get("metadata", {}),
            )
        )
    session.flush()
    return candidate


def evaluate_gate(session: Session, candidate: KnowledgeCandidate) -> str:
    evidence = list(
        session.scalars(
            select(EvidenceRecord).where(EvidenceRecord.candidate_id == candidate.id)
        )
    )
    verified_types = {item.evidence_type for item in evidence if item.verified}
    failed_command = any(
        item.verified and item.exit_code is not None and item.exit_code != 0
        for item in evidence
    )
    successful_command = any(item.verified and item.exit_code == 0 for item in evidence)
    passed = False
    if candidate.category == "error_resolution":
        passed = (
            failed_command
            and successful_command
            and bool(verified_types & {"code_change", "document_version"})
        )
    elif candidate.category in {"implementation", "custom_success"}:
        passed = (
            bool(verified_types & {"code_change", "document_version"})
            and bool(verified_types & {"test_pass", "build_pass", "command_success"})
        )
    elif candidate.category == "performance":
        passed = {
            "performance_before",
            "performance_after",
            "load_cause",
        }.issubset(verified_types)
    elif candidate.category == "operations":
        passed = {"incident_failure", "recovery_success"}.issubset(verified_types)
    if not passed:
        candidate.evidence_gate_status = "NEEDS_EVIDENCE"
        candidate.status = "candidate"
        candidate.updated_at = datetime.now(timezone.utc)
        return candidate.evidence_gate_status
    candidate.evidence_gate_status = "VERIFIED"
    candidate.status = "verified"
    candidate.updated_at = datetime.now(timezone.utc)
    return candidate.evidence_gate_status


def invalidate_misclassified_execution_evidence(session: Session) -> dict[str, int]:
    """Retract derived execution evidence that came from an edit tool.

    The original activity event remains immutable. Only the derived evidence
    assertion is marked unverified, and affected automatic candidates are
    removed from the review/publication path without deleting their history.
    """

    rows = session.execute(
        select(EvidenceRecord, ActivityEvent.tool_name)
        .join(ActivityEvent, ActivityEvent.id == EvidenceRecord.activity_id)
        .where(
            EvidenceRecord.verified.is_(True),
            EvidenceRecord.evidence_type.in_(_EXECUTION_EVIDENCE_TYPES),
        )
    )
    now = datetime.now(timezone.utc)
    affected_candidates: set[uuid.UUID] = set()
    invalidated = 0
    for evidence, tool_name in rows:
        if is_execution_tool(tool_name):
            continue
        evidence.verified = False
        evidence.metadata_json = {
            **(evidence.metadata_json or {}),
            "invalidated_at": now.isoformat(),
            "invalidated_reason": "non_execution_tool_misclassified_as_command",
            "source_tool_name": tool_name,
        }
        affected_candidates.add(evidence.candidate_id)
        invalidated += 1
    session.flush()

    activity_only = 0
    for candidate_id in affected_candidates:
        candidate = session.get(KnowledgeCandidate, candidate_id)
        if candidate is None:
            continue
        if evaluate_gate(session, candidate) == "VERIFIED":
            continue
        metadata = dict(candidate.metadata_json or {})
        if metadata.get("auto_generated"):
            candidate.status = "activity_only"
            metadata["quality_gate_status"] = "ACTIVITY_ONLY"
            metadata["quality_gate_reasons"] = [
                "misclassified_edit_event_was_not_execution_evidence"
            ]
            metadata["evidence_repair"] = {
                "repaired_at": now.isoformat(),
                "reason": "non_execution_tool_misclassified_as_command",
            }
            candidate.metadata_json = metadata
            activity_only += 1
    return {
        "invalidated_evidence": invalidated,
        "affected_candidates": len(affected_candidates),
        "reclassified_activity_only": activity_only,
    }


def evaluate_quality(candidate: KnowledgeCandidate) -> tuple[str, list[str]]:
    """Evaluate whether verified activity is reusable canonical knowledge.

    Evidence proves that work happened. It does not prove that the candidate
    explains a reusable cause, decision, or method. Auto-extracted candidates
    therefore need explicit structure before a human can publish them.
    """

    reasons: list[str] = []
    metadata = dict(candidate.metadata_json or {})
    fields = {
        "problem": candidate.problem.strip(),
        "root_cause": candidate.root_cause.strip(),
        "solution": candidate.solution.strip(),
    }
    for name, value in fields.items():
        if len(value) < 12:
            reasons.append(f"{name}_too_short")
    if candidate.root_cause.startswith("Observed implementation in "):
        reasons.append("generic_file_change_is_not_a_cause")
    if candidate.solution.startswith("Changed artifacts:"):
        reasons.append("artifact_list_is_not_a_reusable_solution")
    if metadata.get("auto_generated") and not metadata.get("structured_knowledge"):
        reasons.append("auto_report_missing_reusable_structure")

    curation_validated = (
        metadata.get("approval_policy") == "local_llm_evidence_bound"
        and metadata.get("curation_validation_status") == "PASS"
    )
    status = "PASS" if not reasons and (
        not metadata.get("auto_generated") or curation_validated
    ) else "NEEDS_REVIEW"
    if metadata.get("auto_generated") and not curation_validated:
        reasons.append("local_llm_evidence_validation_required")
    metadata.update(
        {
            "quality_gate_status": status,
            "quality_gate_reasons": reasons,
            "approval_policy": (
                "local_llm_evidence_bound"
                if curation_validated
                else metadata.get("approval_policy", "human_review")
            ),
        }
    )
    candidate.metadata_json = metadata
    return status, reasons


def _evidence_summary(session: Session, candidate_id: uuid.UUID) -> dict[str, Any]:
    evidence = list(
        session.scalars(
            select(EvidenceRecord).where(EvidenceRecord.candidate_id == candidate_id)
        )
    )
    return {
        "verified_count": sum(item.verified for item in evidence),
        "total_count": len(evidence),
        "types": sorted({item.evidence_type for item in evidence}),
        "locators": [item.locator for item in evidence if item.locator],
    }


def _new_revision(
    session: Session,
    case: KnowledgeCase,
    candidate: KnowledgeCandidate,
) -> KnowledgeCaseRevision:
    latest = session.scalar(
        select(func.max(KnowledgeCaseRevision.revision_number)).where(
            KnowledgeCaseRevision.case_id == case.id
        )
    )
    candidate_metadata = dict(candidate.metadata_json or {})
    revision = KnowledgeCaseRevision(
        case_id=case.id,
        revision_number=(latest or 0) + 1,
        content_json={
            "title": candidate.title,
            "problem": candidate.problem,
            "symptom": candidate.symptom,
            "root_cause": candidate.root_cause,
            "solution": candidate.solution,
            "reported_result": candidate.reported_result,
            "verified_result": candidate.verified_result,
            "article_markdown": candidate_metadata.get("article_markdown"),
            "standfirst": candidate_metadata.get("standfirst"),
            "limitations": candidate_metadata.get("limitations"),
            "content_language": candidate_metadata.get("content_language"),
            "curation": candidate_metadata.get("curation"),
            "evidence_bound_claims": candidate_metadata.get(
                "evidence_bound_claims"
            ),
        },
        evidence_summary=_evidence_summary(session, candidate.id),
    )
    session.add(revision)
    session.flush()
    case.current_revision_id = revision.id
    return revision


def _case_metadata_from_candidate(candidate: KnowledgeCandidate) -> dict[str, Any]:
    metadata = dict(candidate.metadata_json or {})
    return {
        key: metadata[key]
        for key in (
            "article_markdown",
            "standfirst",
            "limitations",
            "content_language",
            "curation",
            "evidence_bound_claims",
            "approval_policy",
        )
        if metadata.get(key) is not None
    }


def publish_candidate(
    session: Session, candidate: KnowledgeCandidate
) -> tuple[KnowledgeCase | None, str]:
    if evaluate_gate(session, candidate) != "VERIFIED":
        return None, "NEEDS_EVIDENCE"
    quality_status, _ = evaluate_quality(candidate)
    if quality_status != "PASS":
        candidate.status = "needs_review"
        return None, "NEEDS_REVIEW"
    supersedes_case_id = (candidate.metadata_json or {}).get("supersedes_case_id")
    if supersedes_case_id:
        try:
            target_id = uuid.UUID(str(supersedes_case_id))
        except ValueError:
            candidate.status = "needs_review"
            candidate.evidence_gate_status = "NEEDS_REVIEW"
            return None, "NEEDS_REVIEW"
        target = session.get(KnowledgeCase, target_id)
        if (
            target is None
            or target.status != "verified"
            or target.category != candidate.category
        ):
            candidate.status = "needs_review"
            candidate.evidence_gate_status = "NEEDS_REVIEW"
            return None, "NEEDS_REVIEW"
        collision = session.scalar(
            select(KnowledgeCase).where(
                KnowledgeCase.dedup_key == candidate.dedup_key,
                KnowledgeCase.id != target.id,
            )
        )
        if collision is not None:
            candidate.status = "needs_review"
            candidate.evidence_gate_status = "NEEDS_REVIEW"
            candidate.metadata_json = {
                **candidate.metadata_json,
                "possible_duplicate_case_id": str(collision.id),
            }
            return None, "NEEDS_REVIEW"
        previous_key = target.dedup_key
        target.category = candidate.category
        target.title = candidate.title
        target.problem = candidate.problem
        target.symptom = candidate.symptom
        target.root_cause = candidate.root_cause
        target.solution = candidate.solution
        target.dedup_key = candidate.dedup_key
        target.last_seen_at = datetime.now(timezone.utc)
        target.occurrence_count += 1
        target.metadata_json = {
            **(target.metadata_json or {}),
            **_case_metadata_from_candidate(candidate),
            "previous_dedup_keys": sorted(
                {
                    *(target.metadata_json or {}).get("previous_dedup_keys", []),
                    previous_key,
                }
            ),
            "last_revision_candidate_id": str(candidate.id),
        }
        session.add(
            KnowledgeOccurrence(
                case_id=target.id,
                candidate_id=candidate.id,
                evidence_json=_evidence_summary(session, candidate.id),
            )
        )
        _new_revision(session, target, candidate)
        candidate.status = "published"
        return target, "REVISED_CANONICAL"
    exact = session.scalar(
        select(KnowledgeCase).where(KnowledgeCase.dedup_key == candidate.dedup_key)
    )
    if exact:
        occurrence = session.scalar(
            select(KnowledgeOccurrence).where(
                KnowledgeOccurrence.case_id == exact.id,
                KnowledgeOccurrence.candidate_id == candidate.id,
            )
        )
        if occurrence is None:
            session.add(
                KnowledgeOccurrence(
                    case_id=exact.id,
                    candidate_id=candidate.id,
                    evidence_json=_evidence_summary(session, candidate.id),
                )
            )
            exact.occurrence_count += 1
            exact.last_seen_at = datetime.now(timezone.utc)
            _new_revision(session, exact, candidate)
            exact.metadata_json = {
                **(exact.metadata_json or {}),
                **_case_metadata_from_candidate(candidate),
                "last_revision_candidate_id": str(candidate.id),
            }
        candidate.status = "published"
        return exact, "MERGED_OCCURRENCE"

    cases = list(session.scalars(select(KnowledgeCase).where(KnowledgeCase.status == "verified")))
    candidate_text = " ".join([candidate.problem, candidate.root_cause, candidate.solution])
    for case in cases:
        case_text = " ".join([case.problem, case.root_cause, case.solution])
        if (
            _jaccard(candidate_text, case_text) >= 0.55
            and candidate.similarity_key != similarity_key(case.symptom)
        ):
            candidate.status = "needs_review"
            candidate.evidence_gate_status = "NEEDS_REVIEW"
            candidate.metadata_json = {
                **candidate.metadata_json,
                "possible_duplicate_case_id": str(case.id),
            }
            return None, "NEEDS_REVIEW"

    case = KnowledgeCase(
        category=candidate.category,
        title=candidate.title,
        problem=candidate.problem,
        symptom=candidate.symptom,
        root_cause=candidate.root_cause,
        solution=candidate.solution,
        dedup_key=candidate.dedup_key,
        status="verified",
        metadata_json=_case_metadata_from_candidate(candidate),
    )
    session.add(case)
    session.flush()
    session.add(
        KnowledgeOccurrence(
            case_id=case.id,
            candidate_id=candidate.id,
            evidence_json=_evidence_summary(session, candidate.id),
        )
    )
    _new_revision(session, case, candidate)
    same_symptom = next(
        (item for item in cases if similarity_key(item.symptom) == candidate.similarity_key),
        None,
    )
    if same_symptom:
        relation = (
            "same_symptom_different_cause"
            if _normalize(same_symptom.root_cause) != _normalize(candidate.root_cause)
            else "same_symptom_alternative_solution"
        )
        session.add(
            KnowledgeCaseRelation(
                source_case_id=same_symptom.id,
                target_case_id=case.id,
                relation_type=relation,
            )
        )
    candidate.status = "published"
    return case, "CREATED_CANONICAL"
