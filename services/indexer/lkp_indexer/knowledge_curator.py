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
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .case_pages import materialize_case
from .generation import (
    CuratedKnowledgeArticle,
    GenerationProvider,
    build_generation_provider,
)
from .knowledge import (
    evaluate_gate,
    evaluate_quality,
    publish_candidate,
)
from .service_runtime import assert_mount_guards, service_pid

logger = structlog.get_logger()
_SCHEDULER_KEY = "knowledge_curator.scheduler"
_CITATION = re.compile(r"\[(E\d+)\]")
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
            "--query-gpu=memory.total,memory.used,memory.free,"
            "utilization.gpu,temperature.gpu",
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
        and snapshot.utilization_percent
        <= settings.knowledge_curation_gpu_max_utilization
        and snapshot.temperature_c <= settings.knowledge_curation_gpu_max_temperature
    )


def busy_retry_seconds(check_count: int, settings: Settings) -> int:
    return min(
        settings.knowledge_curation_busy_retry_max_seconds,
        settings.knowledge_curation_busy_retry_base_seconds
        * (2 ** max(0, check_count - 1)),
    )


def render_article(draft: CuratedKnowledgeArticle, language: str) -> str:
    if language == "ko":
        headings = (
            ("상황과 맥락", draft.context),
            ("문제는 어떻게 드러났나", draft.problem),
            ("원인 또는 구현 판단", draft.cause_or_decision),
            ("무엇을 어떻게 바꿨나", draft.implementation),
            ("검증된 결과", draft.verification),
            ("한계와 다음 확인", draft.limitations),
        )
    else:
        headings = (
            ("Context", draft.context),
            ("How the problem appeared", draft.problem),
            ("Cause or implementation decision", draft.cause_or_decision),
            ("What changed", draft.implementation),
            ("Verified result", draft.verification),
            ("Limitations and next checks", draft.limitations),
        )
    lines = [f"> {draft.standfirst}", ""]
    for heading, content in headings:
        lines.extend([f"## {heading}", "", content.strip(), ""])
    return "\n".join(lines).strip()


def _evidence_rows(
    session: Session, candidate: KnowledgeCandidate
) -> list[EvidenceRecord]:
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
                (
                    f"exit_code={item.exit_code}"
                    if item.exit_code is not None
                    else ""
                ),
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


def validate_draft(
    draft: CuratedKnowledgeArticle,
    evidence_map: dict[str, str],
    settings: Settings,
) -> tuple[str, list[str], str]:
    article = render_article(draft, settings.knowledge_content_language)
    reasons: list[str] = []
    if draft.decision != "publish":
        reasons.append(f"model_decision_{draft.decision}")
    if len(draft.title.strip()) < 10:
        reasons.append("title_too_short")
    if not (
        settings.knowledge_curation_min_article_chars
        <= len(article)
        <= settings.knowledge_curation_max_article_chars
    ):
        reasons.append("article_length_out_of_bounds")
    if draft.unsupported_inferences:
        reasons.append("unsupported_inferences_present")

    all_evidence_text = " ".join(evidence_map.values())
    all_source_numbers = {
        token.replace(",", "") for token in _NUMBER.findall(all_evidence_text)
    }
    for name, content in {
        "title": draft.title,
        "standfirst": draft.standfirst,
        "limitations": draft.limitations,
    }.items():
        if any(
            token.replace(",", "") not in all_source_numbers
            for token in _NUMBER.findall(content)
        ):
            reasons.append(f"{name}_unsupported_number")

    sections = {
        "context": draft.context,
        "problem": draft.problem,
        "cause_or_decision": draft.cause_or_decision,
        "implementation": draft.implementation,
        "verification": draft.verification,
    }
    cited_ids: set[str] = set()
    for name, content in sections.items():
        citations = set(_CITATION.findall(content))
        cited_ids.update(citations)
        if len(content.strip()) < 80:
            reasons.append(f"{name}_too_short")
        paragraphs = [
            item.strip() for item in re.split(r"\n\s*\n", content) if item.strip()
        ]
        for paragraph in paragraphs or [content]:
            paragraph_citations = set(_CITATION.findall(paragraph))
            if not paragraph_citations:
                reasons.append(f"{name}_paragraph_missing_citation")
                continue
            invalid = paragraph_citations - evidence_map.keys()
            if invalid:
                reasons.append(f"{name}_invalid_citation")
                continue
            cited_text = " ".join(
                evidence_map[item] for item in paragraph_citations
            )
            source_numbers = {
                token.replace(",", "") for token in _NUMBER.findall(cited_text)
            }
            if any(
                token.replace(",", "") not in source_numbers
                for token in _NUMBER.findall(paragraph)
            ):
                reasons.append(f"{name}_unsupported_number")

    if len(cited_ids) < 2:
        reasons.append("insufficient_evidence_coverage")
    for claim in draft.evidence_claims:
        if not claim.evidence_ids or any(
            item not in evidence_map for item in claim.evidence_ids
        ):
            reasons.append("claim_invalid_citation")
            break
        cited_text = " ".join(evidence_map[item] for item in claim.evidence_ids)
        source_numbers = {
            token.replace(",", "") for token in _NUMBER.findall(cited_text)
        }
        if any(
            token.replace(",", "") not in source_numbers
            for token in _NUMBER.findall(claim.text)
        ):
            reasons.append("claim_unsupported_number")
            break
    lowered = article.casefold()
    if any(term.casefold() in lowered for term in _HYPE):
        reasons.append("inflated_language")
    status = "PASS" if not reasons else "NEEDS_REVIEW"
    return status, sorted(set(reasons)), article


def _setting(session: Session) -> SystemSetting:
    row = session.get(SystemSetting, _SCHEDULER_KEY)
    if row is None:
        row = SystemSetting(key=_SCHEDULER_KEY, value={})
        session.add(row)
        session.flush()
    return row


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
        "completed_busy_cycle_count": int(
            previous.get("completed_busy_cycle_count") or 0
        )
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
                        "type": "code_change",
                        "claim": "mount guard implementation changed",
                        "verified_value": "two sentinel files are checked",
                        "locator": "service_runtime.py",
                        "exit_code": None,
                    },
                    {
                        "id": "E2",
                        "type": "test_pass",
                        "claim": "mount guard unit tests passed",
                        "verified_value": "42 tests passed",
                        "locator": "pytest",
                        "exit_code": 0,
                    },
                ],
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
            },
            "needs_review",
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
            },
            "needs_review",
        ),
    ]


def qualify_provider(
    provider: GenerationProvider,
    settings: Settings,
) -> dict[str, Any]:
    results = []
    for name, payload, expected in _qualification_payloads():
        draft, _digest = provider.curate(
            payload,
            language=settings.knowledge_content_language,
            prompt_version=settings.generation_prompt_version,
        )
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
        validation, reasons, article = validate_draft(
            draft, evidence_map, settings
        )
        actual = "publish" if validation == "PASS" else "needs_review"
        results.append(
            {
                "name": name,
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
                "reasons": reasons,
                "article_chars": len(article),
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
    output_hash = hashlib.sha256(
        draft.model_dump_json().encode("utf-8")
    ).hexdigest()
    metadata = dict(candidate.metadata_json or {})
    curation = {
        "state": (
            "activity_only"
            if draft.decision == "activity_only"
            else "needs_review"
            if validation != "PASS"
            else "validated"
        ),
        "provider": provider.provider,
        "model": provider.model,
        "model_digest": model_digest,
        "prompt_version": settings.generation_prompt_version,
        "generation_parameters": _generation_parameters(provider),
        "input_hash": input_hash,
        "output_hash": output_hash,
        "validation_status": validation,
        "validation_reasons": reasons,
        "decision": draft.decision,
        "decision_reason": draft.decision_reason,
        "curated_at": now.isoformat(),
    }
    metadata["curation"] = curation
    metadata["approval_policy"] = "local_llm_evidence_bound"
    metadata.setdefault("pre_curation_dedup_key", candidate.dedup_key)
    metadata.setdefault("pre_curation_similarity_key", candidate.similarity_key)
    if draft.decision == "activity_only":
        metadata["quality_gate_status"] = "ACTIVITY_ONLY"
        metadata["quality_gate_reasons"] = ["not_reusable_knowledge"]
        candidate.metadata_json = metadata
        candidate.status = "activity_only"
        candidate.updated_at = now
        return "ACTIVITY_ONLY", None
    if validation != "PASS":
        metadata["quality_gate_status"] = "NEEDS_REVIEW"
        metadata["quality_gate_reasons"] = reasons
        metadata["approval_policy"] = "local_llm_evidence_bound"
        candidate.metadata_json = metadata
        candidate.status = "needs_review"
        candidate.updated_at = now
        return "NEEDS_REVIEW", None

    candidate.category = draft.category
    candidate.title = draft.title.strip()
    candidate.problem = draft.problem.strip()
    candidate.symptom = draft.context.strip()
    candidate.root_cause = draft.cause_or_decision.strip()
    candidate.solution = draft.implementation.strip()
    candidate.verified_result = draft.verification.strip()
    # Keep the deterministic pre-curation identity. Rephrasing by a local model
    # must not manufacture a new canonical case for the same source event.
    metadata.update(
        {
            "structured_knowledge": True,
            "content_language": settings.knowledge_content_language,
            "approval_policy": "local_llm_evidence_bound",
            "article_markdown": article,
            "standfirst": draft.standfirst,
            "limitations": draft.limitations,
            "evidence_bound_claims": [
                item.model_dump() for item in draft.evidence_claims
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
        "standfirst": draft.standfirst,
        "limitations": draft.limitations,
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
    state = dict(scheduler.value or {})
    due = _parse_time(state.get("next_attempt_at"))
    if due and due > now:
        return {
            "state": state.get("state", "scheduled"),
            "next_attempt_at": due.isoformat(),
        }

    try:
        snapshot = gpu_probe()
    except Exception as exc:
        scheduler.value = {
            **state,
            "state": "gpu_probe_error",
            "last_error": f"{type(exc).__name__}: {exc}"[:1000],
            "next_attempt_at": (
                now
                + timedelta(
                    seconds=settings.knowledge_curation_busy_retry_base_seconds
                )
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
                now
                + timedelta(
                    seconds=settings.knowledge_curation_exhausted_cooldown_seconds
                )
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
            "fallback_recommended": "gemma4:12b",
            "last_checked_at": now.isoformat(),
            "last_gpu": asdict(snapshot),
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
