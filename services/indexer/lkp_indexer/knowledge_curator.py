from __future__ import annotations

import argparse
import hashlib
import json
import re
import socket
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from lkp.db import SessionLocal
from lkp.logging import configure_logging
from lkp.models import EvidenceRecord, KnowledgeCandidate, SystemSetting
from lkp.settings import Settings, get_settings
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .case_pages import materialize_case
from .generation import (
    CuratedKnowledgeArticle,
    EvidenceBoundParagraph,
    GenerationProvider,
    build_generation_provider,
)
from .knowledge import (
    assess_knowledge_value,
    evaluate_gate,
    evaluate_quality,
    invalidate_misclassified_execution_evidence,
    publish_candidate,
)
from .service_runtime import assert_mount_guards, service_pid

logger = structlog.get_logger()
_SCHEDULER_KEY = "knowledge_curator.scheduler"
_EVIDENCE_REPAIR_KEY = "knowledge.evidence_repair.non_execution_v1"
_VALUE_BACKFILL_KEY = "knowledge.value_backfill.v3"
_CURATION_HARNESS_VERSION = "evidence-gate-v3"
_INLINE_CITATION = re.compile(
    r"\[[^\]\r\n]{1,50}\]|\((?:\s*E\d+\s*,?)+\s*\)",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)*(?:%|ms|MB|GB|초|분|시간)?")
_HYPE = (
    "완벽하게",
    "완전히 해결",
    "혁신적",
    "압도적",
    "무조건",
    "perfectly",
    "completely solved",
    "revolutionary",
    "guaranteed",
)


def style_warnings(article: str) -> list[str]:
    lowered = article.casefold()
    return ["possible_inflated_language"] if any(
        term.casefold() in lowered for term in _HYPE
    ) else []


@dataclass(frozen=True, slots=True)
class GpuSnapshot:
    total_mb: int
    used_mb: int
    free_mb: int
    utilization_percent: int
    temperature_c: int


def probe_gpu() -> GpuSnapshot:
    completed = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    first = completed.stdout.strip().splitlines()[0]
    values = [int(item.strip()) for item in first.split(",")]
    if len(values) != 5:
        raise RuntimeError("unexpected nvidia-smi field count")
    return GpuSnapshot(*values)


def gpu_is_available(snapshot: GpuSnapshot, settings: Settings) -> bool:
    return (
        snapshot.free_mb >= settings.knowledge_curation_gpu_min_free_mb
        and snapshot.utilization_percent <= settings.knowledge_curation_gpu_max_utilization
        and snapshot.temperature_c <= settings.knowledge_curation_gpu_max_temperature
    )


def busy_retry_seconds(check_count: int, settings: Settings) -> int:
    return min(
        settings.knowledge_curation_busy_retry_max_seconds,
        settings.knowledge_curation_busy_retry_base_seconds * (2 ** max(0, check_count - 1)),
    )


def _plain_text(value: str) -> str:
    """Remove model-authored citation-like tokens before deterministic rendering."""

    return re.sub(r"\s{2,}", " ", _INLINE_CITATION.sub("", value)).strip()


def _render_references(evidence_ids: list[str]) -> str:
    unique = list(dict.fromkeys(evidence_ids))
    return " ".join(f"[{item}]" for item in unique)


def _render_paragraph(paragraph: EvidenceBoundParagraph) -> str:
    text_value = _plain_text(paragraph.text)
    references = _render_references(paragraph.evidence_ids)
    return f"{text_value} {references}".strip()


def _section_plain(paragraphs: list[EvidenceBoundParagraph]) -> str:
    return "\n\n".join(_plain_text(item.text) for item in paragraphs).strip()


def _section_markdown(paragraphs: list[EvidenceBoundParagraph]) -> str:
    return "\n\n".join(_render_paragraph(item) for item in paragraphs).strip()


def render_article(draft: CuratedKnowledgeArticle, language: str) -> str:
    if language == "ko":
        headings = (
            ("상황과 맥락", _section_markdown(draft.context)),
            ("문제는 어떻게 드러났나", _section_markdown(draft.problem)),
            ("원인 또는 구현 판단", _section_markdown(draft.cause_or_decision)),
            ("무엇을 어떻게 바꿨나", _section_markdown(draft.implementation)),
            ("검증된 결과", _section_markdown(draft.verification)),
            ("한계와 다음 확인", _section_markdown(draft.limitations)),
        )
    else:
        headings = (
            ("Context", _section_markdown(draft.context)),
            ("How the problem appeared", _section_markdown(draft.problem)),
            (
                "Cause or implementation decision",
                _section_markdown(draft.cause_or_decision),
            ),
            ("What changed", _section_markdown(draft.implementation)),
            ("Verified result", _section_markdown(draft.verification)),
            ("Limitations and next checks", _section_markdown(draft.limitations)),
        )
    standfirst = _plain_text(draft.standfirst)
    standfirst_refs = _render_references(draft.standfirst_evidence_ids)
    lines = [f"> {standfirst} {standfirst_refs}".rstrip(), ""]
    for heading, content in headings:
        if not content.strip():
            continue
        lines.extend([f"## {heading}", "", content.strip(), ""])
    return "\n".join(lines).strip()


def _evidence_rows(session: Session, candidate: KnowledgeCandidate) -> list[EvidenceRecord]:
    return list(
        session.scalars(
            select(EvidenceRecord)
            .where(
                EvidenceRecord.candidate_id == candidate.id,
                EvidenceRecord.verified.is_(True),
            )
            .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
        )
    )


def _payload(
    candidate: KnowledgeCandidate, evidence: list[EvidenceRecord]
) -> tuple[dict[str, Any], dict[str, str]]:
    evidence_map: dict[str, str] = {}
    serialized = []
    for index, item in enumerate(evidence, start=1):
        evidence_id = f"E{index}"
        searchable = " ".join(
            value
            for value in [
                item.claim,
                item.verified_value or "",
                item.locator or "",
                (f"exit_code={item.exit_code}" if item.exit_code is not None else ""),
            ]
            if value
        )
        evidence_map[evidence_id] = searchable
        serialized.append(
            {
                "id": evidence_id,
                "type": item.evidence_type,
                "claim": item.claim,
                "verified_value": item.verified_value,
                "locator": item.locator,
                "exit_code": item.exit_code,
            }
        )
    payload = {
        "candidate": {
            "current_category": candidate.category,
            "title": candidate.title,
            "problem": candidate.problem,
            "symptom": candidate.symptom,
            "root_cause": candidate.root_cause,
            "solution": candidate.solution,
        },
        "reported_result_not_evidence": candidate.reported_result,
        "verified_evidence": serialized,
        "deterministic_value_assessment": (candidate.metadata_json or {}).get("knowledge_value"),
        "publication_rule": (
            "Choose publish only when verified evidence supports a reusable, non-inflated "
            "article. Reported text may provide context but is not verified evidence."
        ),
    }
    return payload, evidence_map


def _payload_hash(
    payload: dict[str, Any],
    prompt_version: str,
    generation_parameters: dict[str, Any],
) -> str:
    encoded = json.dumps(
        {
            "prompt_version": prompt_version,
            "generation_parameters": generation_parameters,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _generation_parameters(provider: GenerationProvider) -> dict[str, Any]:
    value = getattr(provider, "generation_parameters", {})
    return dict(value) if isinstance(value, dict) else {}


def _scheduler_revision(settings: Settings) -> str:
    payload = {
        "harness_version": _CURATION_HARNESS_VERSION,
        "model": settings.generation_model,
        "model_digest": settings.generation_model_digest,
        "prompt_version": settings.generation_prompt_version,
        "temperature": settings.generation_temperature,
        "context_window": settings.generation_context_window,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _fallback_models(settings: Settings) -> list[str]:
    return [
        item.strip()
        for item in settings.generation_fallback_models.split(",")
        if item.strip() and item.strip() != settings.generation_model
    ]


def validate_draft(
    draft: CuratedKnowledgeArticle,
    evidence_map: dict[str, str],
    settings: Settings,
) -> tuple[str, list[str], str]:
    if not draft.standfirst_evidence_ids:
        explicit_ids = list(dict.fromkeys(re.findall(r"(?<!\w)E\d+(?!\w)", draft.standfirst)))
        if explicit_ids:
            clean_standfirst = re.sub(
                r"(?:\s*[,;]?\s*E\d+)+[.!]?\s*$",
                "",
                draft.standfirst,
            ).rstrip(" ,;")
            draft = draft.model_copy(
                update={
                    "standfirst": clean_standfirst,
                    "standfirst_evidence_ids": explicit_ids,
                }
            )
    article = render_article(draft, settings.knowledge_content_language)
    reasons: list[str] = []
    if not draft.title.strip():
        reasons.append("title_missing")
    if len(article) > settings.knowledge_curation_max_article_chars:
        reasons.append("article_too_long")

    all_evidence_text = " ".join(evidence_map.values())
    all_source_numbers = {token.replace(",", "") for token in _NUMBER.findall(all_evidence_text)}
    for name, content in {
        "title": draft.title,
    }.items():
        if any(
            token.replace(",", "") not in all_source_numbers for token in _NUMBER.findall(content)
        ):
            reasons.append(f"{name}_unsupported_number")

    if not draft.standfirst_evidence_ids:
        reasons.append("standfirst_missing_citation")
    elif any(item not in evidence_map for item in draft.standfirst_evidence_ids):
        reasons.append("standfirst_invalid_citation")
    else:
        cited_text = " ".join(evidence_map[item] for item in draft.standfirst_evidence_ids)
        source_numbers = {token.replace(",", "") for token in _NUMBER.findall(cited_text)}
        if any(
            token.replace(",", "") not in source_numbers
            for token in _NUMBER.findall(_plain_text(draft.standfirst))
        ):
            reasons.append("standfirst_unsupported_number")

    sections: dict[str, list[EvidenceBoundParagraph]] = {
        "context": draft.context,
        "problem": draft.problem,
        "cause_or_decision": draft.cause_or_decision,
        "implementation": draft.implementation,
        "verification": draft.verification,
        "limitations": draft.limitations,
    }
    cited_ids: set[str] = set()
    required_sections = {
        "problem",
        "cause_or_decision",
        "implementation",
        "verification",
    }
    for name, paragraphs in sections.items():
        if name in required_sections and not paragraphs:
            reasons.append(f"{name}_missing")
        for paragraph in paragraphs:
            paragraph_citations = set(paragraph.evidence_ids)
            cited_ids.update(paragraph_citations)
            if not paragraph_citations:
                reasons.append(f"{name}_paragraph_missing_citation")
                continue
            invalid = paragraph_citations - evidence_map.keys()
            if invalid:
                reasons.append(f"{name}_invalid_citation")
                continue
            cited_text = " ".join(evidence_map[item] for item in paragraph_citations)
            source_numbers = {token.replace(",", "") for token in _NUMBER.findall(cited_text)}
            if any(
                token.replace(",", "") not in source_numbers
                for token in _NUMBER.findall(_plain_text(paragraph.text))
            ):
                reasons.append(f"{name}_unsupported_number")

    if len(cited_ids) < 2:
        reasons.append("insufficient_evidence_coverage")
    status = "PASS" if not reasons else "NEEDS_REVIEW"
    return status, sorted(set(reasons)), article


def _setting(session: Session) -> SystemSetting:
    row = session.get(SystemSetting, _SCHEDULER_KEY)
    if row is None:
        row = SystemSetting(key=_SCHEDULER_KEY, value={})
        session.add(row)
        session.flush()
    return row


def _ensure_evidence_repair(session: Session, now: datetime) -> dict[str, Any]:
    existing = session.get(SystemSetting, _EVIDENCE_REPAIR_KEY)
    if existing is not None:
        return dict(existing.value or {})
    report = invalidate_misclassified_execution_evidence(session)
    value = {**report, "completed_at": now.isoformat()}
    session.add(SystemSetting(key=_EVIDENCE_REPAIR_KEY, value=value))
    session.flush()
    return value


def _ensure_value_backfill(session: Session, now: datetime) -> dict[str, Any]:
    existing = session.get(SystemSetting, _VALUE_BACKFILL_KEY)
    if existing is not None:
        return dict(existing.value or {})
    counts = {"assessed": 0, "promote": 0, "needs_review": 0, "activity_only": 0}
    for candidate in session.scalars(select(KnowledgeCandidate)):
        evidence = _evidence_rows(session, candidate)
        metadata = dict(candidate.metadata_json or {})
        assessment = assess_knowledge_value(
            category=candidate.category,
            problem=candidate.problem,
            root_cause=candidate.root_cause,
            solution=candidate.solution,
            evidence=evidence,
            metadata=metadata,
        )
        metadata["knowledge_value"] = assessment
        candidate.metadata_json = metadata
        counts["assessed"] += 1
        counts[assessment["tier"]] += 1
        if candidate.status == "published":
            continue
        if assessment["tier"] == "activity_only":
            candidate.status = "activity_only"
        elif assessment["tier"] == "needs_review":
            candidate.status = "needs_review"
    value = {**counts, "completed_at": now.isoformat()}
    session.add(SystemSetting(key=_VALUE_BACKFILL_KEY, value=value))
    session.flush()
    return value


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _record_gpu_wait(
    row: SystemSetting,
    snapshot: GpuSnapshot,
    settings: Settings,
    now: datetime,
) -> dict[str, Any]:
    previous = dict(row.value or {})
    count = int(previous.get("busy_check_count") or 0) + 1
    exhausted = count >= settings.knowledge_curation_busy_max_checks
    delay = (
        settings.knowledge_curation_exhausted_cooldown_seconds
        if exhausted
        else busy_retry_seconds(count, settings)
    )
    state = "gpu_wait_cooldown" if exhausted else "waiting_for_gpu"
    row.value = {
        **previous,
        "state": state,
        "busy_check_count": 0 if exhausted else count,
        "completed_busy_cycle_count": int(previous.get("completed_busy_cycle_count") or 0)
        + (1 if exhausted else 0),
        "next_attempt_at": (now + timedelta(seconds=delay)).isoformat(),
        "last_checked_at": now.isoformat(),
        "last_gpu": asdict(snapshot),
        "retry_seconds": delay,
        "max_checks_per_cycle": settings.knowledge_curation_busy_max_checks,
    }
    row.updated_at = now
    return row.value


def _record_failure(
    row: SystemSetting,
    *,
    state_name: str,
    error: Exception,
    snapshot: GpuSnapshot,
    settings: Settings,
    now: datetime,
) -> dict[str, Any]:
    previous = dict(row.value or {})
    count = int(previous.get("failure_count") or 0) + 1
    exhausted = count >= settings.knowledge_curation_busy_max_checks
    delay = (
        settings.knowledge_curation_exhausted_cooldown_seconds
        if exhausted
        else busy_retry_seconds(count, settings)
    )
    row.value = {
        **previous,
        "state": f"{state_name}_cooldown" if exhausted else state_name,
        "failure_count": 0 if exhausted else count,
        "last_error": f"{type(error).__name__}: {error}"[:1000],
        "last_checked_at": now.isoformat(),
        "last_gpu": asdict(snapshot),
        "next_attempt_at": (now + timedelta(seconds=delay)).isoformat(),
        "retry_seconds": delay,
    }
    row.updated_at = now
    return row.value


def _qualification_payloads() -> list[tuple[str, dict[str, Any], str]]:
    return [
        (
            "supported_implementation",
            {
                "candidate": {
                    "current_category": "implementation",
                    "title": "재시작 후 마운트 검증 추가",
                    "problem": "빈 bind mount로 서비스가 잘못 준비 상태가 됐다.",
                    "symptom": "API는 준비 상태였지만 데이터 경로가 비어 있었다.",
                    "root_cause": "필수 경로의 존재만 검사하고 파일 여부는 검사하지 않았다.",
                    "solution": "sentinel 파일과 소스 파일을 시작 시 확인하도록 바꿨다.",
                },
                "reported_result_not_evidence": "문제가 완전히 해결됐다.",
                "verified_evidence": [
                    {
                        "id": "E1",
                        "type": "incident_observation",
                        "claim": "the readiness probe accepted an incomplete mount",
                        "verified_value": (
                            "reproduction returned HTTP 200 while a required "
                            "sentinel file was absent"
                        ),
                        "locator": "mount-guard reproduction",
                        "exit_code": None,
                    },
                    {
                        "id": "E2",
                        "type": "code_change",
                        "claim": "startup mount validation changed",
                        "verified_value": (
                            "settings.yaml and source-roots.yaml must both exist "
                            "before the service starts"
                        ),
                        "locator": "service_runtime.py",
                        "exit_code": None,
                    },
                    {
                        "id": "E3",
                        "type": "test_pass",
                        "claim": "mount guard unit tests passed",
                        "verified_value": "42 tests passed",
                        "locator": "pytest",
                        "exit_code": 0,
                    },
                    {
                        "id": "E4",
                        "type": "integration_test",
                        "claim": (
                            "both the rejected incomplete mount and accepted "
                            "complete mount paths were exercised"
                        ),
                        "verified_value": "2 integration scenarios passed",
                        "locator": "mount guard integration fixture",
                        "exit_code": 0,
                    },
                    {
                        "id": "E5",
                        "type": "scope_limit",
                        "claim": "long-duration suspend and resume was not tested",
                        "verified_value": (
                            "suspend and resume endurance is excluded from the "
                            "current validation scope"
                        ),
                        "locator": "validation-report.md",
                        "exit_code": None,
                    },
                ],
                "deterministic_value_assessment": {
                    "revision": "knowledge-value-v1",
                    "tier": "promote",
                    "publication_eligible": True,
                    "signals": [
                        "verified_change_and_validation",
                        "reusable_explanation_present",
                    ],
                    "blockers": [],
                },
            },
            "publish",
        ),
        (
            "reported_only",
            {
                "candidate": {
                    "current_category": "implementation",
                    "title": "성능을 크게 개선했다는 보고",
                    "problem": "느린 검색",
                    "symptom": "느리다고 보고됨",
                    "root_cause": "확인되지 않음",
                    "solution": "캐시를 추가했다고 보고됨",
                },
                "reported_result_not_evidence": "검색이 10배 빨라졌다.",
                "verified_evidence": [],
                "deterministic_value_assessment": {
                    "revision": "knowledge-value-v1",
                    "tier": "activity_only",
                    "publication_eligible": False,
                    "signals": [],
                    "blockers": ["no_verified_evidence"],
                },
            },
            "held",
        ),
        (
            "unmeasured_performance",
            {
                "candidate": {
                    "current_category": "performance",
                    "title": "부하를 낮췄다는 보고",
                    "problem": "CPU 부하",
                    "symptom": "높은 CPU라고 보고됨",
                    "root_cause": "원인 미측정",
                    "solution": "설정을 변경했다고 보고됨",
                },
                "reported_result_not_evidence": "부하가 크게 감소했다.",
                "verified_evidence": [
                    {
                        "id": "E1",
                        "type": "code_change",
                        "claim": "configuration changed",
                        "verified_value": "file hash changed",
                        "locator": "compose.yaml",
                        "exit_code": None,
                    }
                ],
                "deterministic_value_assessment": {
                    "revision": "knowledge-value-v1",
                    "tier": "needs_review",
                    "publication_eligible": False,
                    "signals": [],
                    "blockers": ["before_after_and_load_cause_required"],
                },
            },
            "held",
        ),
    ]


def qualify_provider(
    provider: GenerationProvider,
    settings: Settings,
) -> dict[str, Any]:
    results = []
    for name, payload, expected in _qualification_payloads():
        value_assessment = payload.get("deterministic_value_assessment") or {}
        if value_assessment.get("tier") != "promote":
            results.append(
                {
                    "name": name,
                    "expected": expected,
                    "actual": "held",
                    "passed": expected == "held",
                    "reasons": list(value_assessment.get("blockers") or []),
                    "decision": "deterministic_harness_hold",
                    "article_chars": 0,
                }
            )
            continue
        try:
            draft, _digest = provider.curate(
                payload,
                language=settings.knowledge_content_language,
                prompt_version=settings.generation_prompt_version,
            )
        except ValidationError as exc:
            results.append(
                {
                    "name": name,
                    "expected": expected,
                    "actual": "invalid_structured_output",
                    "passed": False,
                    "reasons": ["pydantic_validation_failed"],
                    "validation_error_count": exc.error_count(),
                }
            )
            continue
        evidence_map = {
            item["id"]: " ".join(
                str(value)
                for value in [
                    item.get("claim"),
                    item.get("verified_value"),
                    item.get("locator"),
                    (
                        f"exit_code={item.get('exit_code')}"
                        if item.get("exit_code") is not None
                        else ""
                    ),
                ]
                if value
            )
            for item in payload["verified_evidence"]
        }
        validation, reasons, article = validate_draft(draft, evidence_map, settings)
        actual = "publish" if validation == "PASS" else "held"
        results.append(
            {
                "name": name,
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
                "reasons": reasons,
                "article_chars": len(article),
                "decision": draft.decision,
                "decision_reason": draft.decision_reason,
                "article_markdown": article,
                "style_warnings": style_warnings(article),
            }
        )
    passed = all(item["passed"] for item in results)
    return {"status": "PASS" if passed else "FAIL", "results": results}


def _qualification_key(
    model: str,
    digest: str,
    prompt_version: str,
    generation_parameters: dict[str, Any],
) -> str:
    identity = hashlib.sha256(
        json.dumps(
            {
                "harness_version": _CURATION_HARNESS_VERSION,
                "model": model,
                "digest": digest,
                "prompt_version": prompt_version,
                "generation_parameters": generation_parameters,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()[:24]
    return f"knowledge_curator.qualification.{identity}"


def curate_candidate(
    session: Session,
    candidate: KnowledgeCandidate,
    provider: GenerationProvider,
    settings: Settings,
    model_digest: str,
) -> tuple[str, str | None]:
    evidence = _evidence_rows(session, candidate)
    metadata = dict(candidate.metadata_json or {})
    value_assessment = assess_knowledge_value(
        category=candidate.category,
        problem=candidate.problem,
        root_cause=candidate.root_cause,
        solution=candidate.solution,
        evidence=evidence,
        metadata=metadata,
    )
    metadata["knowledge_value"] = value_assessment
    candidate.metadata_json = metadata
    if value_assessment["tier"] != "promote":
        value_hash = hashlib.sha256(
            json.dumps(
                value_assessment,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        previous_curation = dict(metadata.get("curation") or {})
        if (
            previous_curation.get("validation_status") == "VALUE_HARNESS_REJECTED"
            and previous_curation.get("value_hash") == value_hash
            and previous_curation.get("prompt_version") == settings.generation_prompt_version
        ):
            return "UNCHANGED", None
        candidate.status = (
            "activity_only" if value_assessment["tier"] == "activity_only" else "needs_review"
        )
        metadata["curation"] = {
            "state": candidate.status,
            "validation_status": "VALUE_HARNESS_REJECTED",
            "validation_reasons": value_assessment["blockers"],
            "prompt_version": settings.generation_prompt_version,
            "value_hash": value_hash,
        }
        candidate.metadata_json = metadata
        candidate.updated_at = datetime.now(timezone.utc)
        return (
            "ACTIVITY_ONLY"
            if candidate.status == "activity_only"
            else "VALUE_HARNESS_NEEDS_REVIEW",
            None,
        )

    payload, evidence_map = _payload(candidate, evidence)
    input_hash = _payload_hash(
        payload,
        settings.generation_prompt_version,
        _generation_parameters(provider),
    )
    previous = dict((candidate.metadata_json or {}).get("curation") or {})
    if (
        previous.get("input_hash") == input_hash
        and previous.get("model_digest") == model_digest
        and previous.get("state") in {"published", "needs_review", "activity_only"}
    ):
        return "UNCHANGED", None

    draft, response_digest = provider.curate(
        payload,
        language=settings.knowledge_content_language,
        prompt_version=settings.generation_prompt_version,
    )
    if response_digest != model_digest:
        raise RuntimeError("generation model digest changed during curation")
    validation, reasons, article = validate_draft(draft, evidence_map, settings)
    now = datetime.now(timezone.utc)
    output_hash = hashlib.sha256(draft.model_dump_json().encode("utf-8")).hexdigest()
    metadata = dict(candidate.metadata_json or {})
    curation = {
        "state": "needs_review" if validation != "PASS" else "validated",
        "provider": provider.provider,
        "model": provider.model,
        "model_digest": model_digest,
        "prompt_version": settings.generation_prompt_version,
        "generation_parameters": _generation_parameters(provider),
        "input_hash": input_hash,
        "output_hash": output_hash,
        "validation_status": validation,
        "validation_reasons": reasons,
        "editorial_recommendation": draft.decision,
        "decision_reason": draft.decision_reason,
        "style_warnings": style_warnings(article),
        "unsupported_inferences_omitted": draft.unsupported_inferences,
        "curated_at": now.isoformat(),
    }
    metadata["curation"] = curation
    metadata["approval_policy"] = "deterministic_evidence_gate_with_llm_editor"
    metadata.setdefault("pre_curation_dedup_key", candidate.dedup_key)
    metadata.setdefault("pre_curation_similarity_key", candidate.similarity_key)
    if validation != "PASS":
        metadata["quality_gate_status"] = "NEEDS_REVIEW"
        metadata["quality_gate_reasons"] = reasons
        metadata["approval_policy"] = "deterministic_evidence_gate_with_llm_editor"
        candidate.metadata_json = metadata
        candidate.status = "needs_review"
        candidate.updated_at = now
        return "NEEDS_REVIEW", None

    candidate.title = draft.title.strip()
    candidate.problem = _section_plain(draft.problem)
    candidate.symptom = _section_plain(draft.context)
    candidate.root_cause = _section_plain(draft.cause_or_decision)
    candidate.solution = _section_plain(draft.implementation)
    candidate.verified_result = _section_plain(draft.verification)
    # Keep the deterministic pre-curation identity. Rephrasing by a local model
    # must not manufacture a new canonical case for the same source event.
    metadata.update(
        {
            "structured_knowledge": True,
            "content_language": settings.knowledge_content_language,
            "approval_policy": "deterministic_evidence_gate_with_llm_editor",
            "article_markdown": article,
            "standfirst": _plain_text(draft.standfirst),
            "limitations": _section_plain(draft.limitations),
            "evidence_bound_claims": [
                {"section": section, **item.model_dump()}
                for section, paragraphs in {
                    "context": draft.context,
                    "problem": draft.problem,
                    "cause_or_decision": draft.cause_or_decision,
                    "implementation": draft.implementation,
                    "verification": draft.verification,
                    "limitations": draft.limitations,
                }.items()
                for item in paragraphs
            ],
            "curation_validation_status": "PASS",
        }
    )
    candidate.metadata_json = metadata
    if evaluate_gate(session, candidate) != "VERIFIED":
        curation["state"] = "needs_review"
        curation["validation_reasons"] = ["classification_evidence_gate_failed"]
        candidate.status = "needs_review"
        return "NEEDS_EVIDENCE", None
    quality, quality_reasons = evaluate_quality(candidate)
    if quality != "PASS":
        curation["state"] = "needs_review"
        curation["validation_reasons"] = quality_reasons
        candidate.status = "needs_review"
        return "NEEDS_REVIEW", None
    if not settings.knowledge_curation_auto_publish:
        curation["state"] = "validated"
        candidate.status = "verified"
        return "VALIDATED_NOT_PUBLISHED", None

    case, outcome = publish_candidate(session, candidate)
    if case is None:
        curation["state"] = "needs_review"
        candidate.metadata_json = {**metadata, "curation": dict(curation)}
        return outcome, None
    curation["state"] = "published"
    candidate.metadata_json = {**metadata, "curation": dict(curation)}
    case.metadata_json = {
        **(case.metadata_json or {}),
        "article_markdown": article,
        "standfirst": _plain_text(draft.standfirst),
        "limitations": _section_plain(draft.limitations),
        "curation": dict(curation),
    }
    materialize_case(
        session,
        case,
        vault_dir=settings.vault_dir,
        pipeline_version=settings.pipeline_version,
        content_language=settings.knowledge_content_language,
    )
    return outcome, str(case.id)


def run_once(
    session: Session,
    settings: Settings,
    *,
    provider: GenerationProvider | None = None,
    gpu_probe=probe_gpu,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    lock_acquired = session.execute(
        text("select pg_try_advisory_xact_lock(hashtext(:key))"),
        {"key": _SCHEDULER_KEY},
    ).scalar_one()
    if not lock_acquired:
        return {"state": "standby_lock_held"}
    scheduler = _setting(session)
    evidence_repair = _ensure_evidence_repair(session, now)
    value_backfill = _ensure_value_backfill(session, now)
    state = dict(scheduler.value or {})
    scheduler_revision = _scheduler_revision(settings)
    if state.get("generation_revision") != scheduler_revision:
        state = {
            "generation_revision": scheduler_revision,
            "state": "configuration_changed",
            "busy_check_count": 0,
            "failure_count": 0,
        }
        scheduler.value = state
        scheduler.updated_at = now
        session.flush()
    due = _parse_time(state.get("next_attempt_at"))
    if due and due > now:
        return {
            "state": state.get("state", "scheduled"),
            "next_attempt_at": due.isoformat(),
            "evidence_repair": evidence_repair,
            "value_backfill": value_backfill,
        }

    try:
        snapshot = gpu_probe()
    except Exception as exc:
        scheduler.value = {
            **state,
            "state": "gpu_probe_error",
            "last_error": f"{type(exc).__name__}: {exc}"[:1000],
            "next_attempt_at": (
                now + timedelta(seconds=settings.knowledge_curation_busy_retry_base_seconds)
            ).isoformat(),
            "last_checked_at": now.isoformat(),
        }
        scheduler.updated_at = now
        return scheduler.value
    if not gpu_is_available(snapshot, settings):
        return _record_gpu_wait(scheduler, snapshot, settings, now)

    provider = provider or build_generation_provider(settings)
    if provider is None:
        scheduler.value = {
            **state,
            "state": "disabled",
            "last_checked_at": now.isoformat(),
            "last_gpu": asdict(snapshot),
        }
        scheduler.updated_at = now
        return scheduler.value

    try:
        model_digest = provider.model_digest()
    except Exception as exc:
        scheduler.value = {
            **state,
            "state": "model_unavailable",
            "last_error": f"{type(exc).__name__}: {exc}"[:1000],
            "next_attempt_at": (
                now + timedelta(seconds=settings.knowledge_curation_exhausted_cooldown_seconds)
            ).isoformat(),
            "last_checked_at": now.isoformat(),
            "last_gpu": asdict(snapshot),
        }
        scheduler.updated_at = now
        return scheduler.value

    qualification_key = _qualification_key(
        provider.model,
        model_digest,
        settings.generation_prompt_version,
        _generation_parameters(provider),
    )
    qualification = session.get(SystemSetting, qualification_key)
    if qualification is None:
        try:
            report = qualify_provider(provider, settings)
        except Exception as exc:
            return _record_failure(
                scheduler,
                state_name="qualification_error",
                error=exc,
                snapshot=snapshot,
                settings=settings,
                now=now,
            )
        qualification = SystemSetting(
            key=qualification_key,
            value={
                **report,
                "harness_version": _CURATION_HARNESS_VERSION,
                "provider": provider.provider,
                "model": provider.model,
                "model_digest": model_digest,
                "prompt_version": settings.generation_prompt_version,
                "generation_parameters": _generation_parameters(provider),
                "qualified_at": now.isoformat(),
            },
        )
        session.add(qualification)
        session.flush()
    if qualification.value.get("status") != "PASS":
        scheduler.value = {
            **state,
            "state": "model_rejected",
            "model": provider.model,
            "model_digest": model_digest,
            "qualification_key": qualification_key,
            "fallback_recommended": _fallback_models(settings),
            "last_checked_at": now.isoformat(),
            "last_gpu": asdict(snapshot),
            "last_error": None,
            "failure_count": 0,
            "retry_seconds": None,
            "next_attempt_at": None,
        }
        scheduler.updated_at = now
        return scheduler.value

    candidates = list(
        session.scalars(
            select(KnowledgeCandidate)
            .where(
                KnowledgeCandidate.status.in_(["candidate", "verified", "needs_review"]),
                KnowledgeCandidate.evidence_gate_status == "VERIFIED",
            )
            .order_by(KnowledgeCandidate.updated_at)
        )
    )
    outcomes: list[dict[str, str | None]] = []
    for candidate in candidates:
        if len(outcomes) >= settings.knowledge_curation_batch_size:
            break
        try:
            with session.begin_nested():
                outcome, case_id = curate_candidate(
                    session, candidate, provider, settings, model_digest
                )
        except Exception as exc:
            return _record_failure(
                scheduler,
                state_name="curation_error",
                error=exc,
                snapshot=snapshot,
                settings=settings,
                now=now,
            )
        if outcome != "UNCHANGED":
            outcomes.append(
                {
                    "candidate_id": str(candidate.id),
                    "outcome": outcome,
                    "case_id": case_id,
                }
            )
    scheduler.value = {
        **state,
        "state": "idle" if not outcomes else "completed_batch",
        "busy_check_count": 0,
        "failure_count": 0,
        "last_error": None,
        "retry_seconds": None,
        "last_checked_at": now.isoformat(),
        "last_completed_at": now.isoformat(),
        "next_attempt_at": (
            now + timedelta(seconds=settings.knowledge_curation_poll_seconds)
        ).isoformat(),
        "last_gpu": asdict(snapshot),
        "provider": provider.provider,
        "model": provider.model,
        "model_digest": model_digest,
        "qualification_key": qualification_key,
        "last_outcomes": outcomes,
        "hostname": socket.gethostname(),
    }
    scheduler.updated_at = now
    return scheduler.value


def run_forever(settings: Settings) -> int:
    while True:
        try:
            with SessionLocal() as session:
                result = run_once(session, settings)
                session.commit()
            logger.info("knowledge_curator_tick", **result)
        except Exception as exc:
            logger.exception(
                "knowledge_curator_tick_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:1000],
            )
        time.sleep(settings.knowledge_curation_poll_seconds)


def main() -> int:
    configure_logging("knowledge-curator")
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--probe-gpu", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if args.probe_gpu:
        snapshot = probe_gpu()
        print(
            json.dumps(
                {
                    **asdict(snapshot),
                    "available": gpu_is_available(snapshot, settings),
                },
                ensure_ascii=False,
            )
        )
        return 0
    if not settings.knowledge_curation_enabled:
        logger.info("knowledge_curator_disabled")
        return 0
    assert_mount_guards(settings)
    with service_pid():
        if args.once:
            with SessionLocal() as session:
                result = run_once(session, settings)
                session.commit()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        return run_forever(settings)


if __name__ == "__main__":
    raise SystemExit(main())
