from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from lkp.models import (
    EvidenceRecord,
    KnowledgeCandidate,
    KnowledgeCase,
    KnowledgeCaseRelation,
    KnowledgeCaseRevision,
    KnowledgeOccurrence,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _normalize(value: str) -> str:
    return " ".join(re.findall(r"\w+", value.casefold(), flags=re.UNICODE))


def _hash(*values: str) -> str:
    return hashlib.sha256("\x1f".join(_normalize(item) for item in values).encode()).hexdigest()


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
        },
        evidence_summary=_evidence_summary(session, candidate.id),
    )
    session.add(revision)
    session.flush()
    case.current_revision_id = revision.id
    return revision


def publish_candidate(
    session: Session, candidate: KnowledgeCandidate
) -> tuple[KnowledgeCase | None, str]:
    if evaluate_gate(session, candidate) != "VERIFIED":
        return None, "NEEDS_EVIDENCE"
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
