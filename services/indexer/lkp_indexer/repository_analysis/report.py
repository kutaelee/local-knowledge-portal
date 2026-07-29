from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any

from .domain import (
    AnalysisManifest,
    Claim,
    Confidence,
    EvidenceReference,
    KnowledgeItem,
    LifecycleEdge,
    LifecycleNode,
    ValidationStatus,
)

_REPORT_SYSTEM = """
당신은 저장소 전체를 처음 읽는 개발자를 위한 기술 보고서를 작성합니다.
입력에는 원본 검증을 통과한 claim_catalog만 있습니다.

규칙:
- 한국어로 자연스럽고 구체적으로 작성합니다.
- 저장소가 무엇을 하는지, 핵심 기술이 어떤 역할을 하는지, 입력부터 종료까지
  어떤 순서로 처리하는지를 설명합니다.
- 각 문장은 반드시 그 문장을 직접 뒷받침하는 claim_id를 하나 이상 인용합니다.
- 입력에 없는 기능, 기술, 실행 순서, 운영 사실을 추측하지 않습니다.
- 정적 근거만으로 알 수 없는 실행 환경과 배포 동작은 unknowns에 남깁니다.
- 클래스 목록을 나열하는 대신 사람이 전체 구조를 이해할 수 있는 수준으로 묶습니다.
- purpose는 1~2문장, capabilities는 3~5개, technologies는 4~8개,
  processing_flow는 3~6개, operational_notes는 2~5개로 작성합니다.
- 각 항목은 한두 문장으로 간결하게 작성하고 같은 사실을 반복하지 않습니다.
- 문장에는 인용한 Claim의 핵심 명사, 기술명 또는 식별자를 그대로 포함해
  문장과 근거의 연결을 기계적으로 검증할 수 있게 합니다.
""".strip()

_PHASES = {
    "STARTUP",
    "DISCOVERY",
    "INPUT",
    "PROCESSING",
    "VALIDATION",
    "PERSISTENCE",
    "OUTPUT",
    "SHUTDOWN",
    "SUPPORT",
}

_REPORT_CONTRACT = "human-readable-v2"

_OVERVIEW_MARKERS = (
    "main",
    "bootstrap",
    "container",
    "broker",
    "service",
    "endpoint",
    "flow",
    "route",
    "process",
    "handler",
    "consumer",
    "provider",
    "lifecycle",
    "start",
    "init",
    "stop",
    "deploy",
    "message",
    "컨테이너",
    "브로커",
    "서비스",
    "엔드포인트",
    "흐름",
    "처리",
    "메시지",
    "시작",
    "종료",
    "배포",
)

_SUPPORT_TOKEN = re.compile(r"[\w./:-]{2,}", re.UNICODE)
_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.:/-]{2,}\b")
_GENERIC_SUPPORT_TERMS = {
    "그리고",
    "그러나",
    "대한",
    "위한",
    "통해",
    "관련",
    "역할",
    "기능",
    "처리",
    "구성",
    "저장소",
    "합니다",
    "입니다",
    "with",
    "from",
    "into",
    "that",
    "this",
    "uses",
}


def _claim_score(claim: Claim) -> tuple[int, str, str]:
    text = f"{claim.component} {claim.claim}".casefold()
    score = sum(marker in text for marker in _OVERVIEW_MARKERS)
    if claim.claim_type == "DESIGN_INFERENCE":
        score += 3
    elif claim.claim_type in {"CODE_FACT", "CONFIGURATION_FACT", "DEPENDENCY_FACT"}:
        score += 1
    return (-score, claim.component.casefold(), claim.claim.casefold())


def _claim_catalog(
    manifest: AnalysisManifest,
    *,
    limit: int = 120,
    max_serialized_chars: int = 22_000,
    max_claim_chars: int = 800,
) -> tuple[list[dict[str, Any]], dict[str, Claim]]:
    ranked = sorted(
        (
            claim
            for claim in manifest.claims
            if claim.validation_status == ValidationStatus.SOURCE_VERIFIED
            and claim.evidence
            and len(claim.claim) <= max_claim_chars
        ),
        key=_claim_score,
    )
    # Preserve breadth before adding more high-scoring details. Large legacy
    # repositories otherwise let one framework-heavy component consume the
    # whole bounded prompt and hide the actual repository-wide lifecycle.
    verified: list[Claim] = []
    seen_components: set[str] = set()
    selected_ids: set[int] = set()
    serialized_chars = 2  # JSON array delimiters.

    def add(claim: Claim) -> bool:
        nonlocal serialized_chars
        preview = {
            "id": "C000",
            "claim": claim.claim,
            "claim_type": claim.claim_type,
            "component": claim.component,
            "evidence": [
                {
                    "file": reference.file,
                    "start_line": reference.start_line,
                    "end_line": reference.end_line,
                    "symbol": reference.symbol,
                }
                for reference in claim.evidence[:1]
            ],
        }
        encoded_chars = len(
            json.dumps(preview, ensure_ascii=False, separators=(",", ":"))
        )
        separator_chars = 1 if verified else 0
        if serialized_chars + separator_chars + encoded_chars > max_serialized_chars:
            return False
        serialized_chars += separator_chars + encoded_chars
        selected_ids.add(id(claim))
        verified.append(claim)
        return True

    for claim in ranked:
        component = claim.component.casefold()
        if component in seen_components:
            continue
        if not add(claim):
            continue
        seen_components.add(component)
        if len(verified) == limit:
            break
    if len(verified) < limit:
        for claim in ranked:
            if id(claim) in selected_ids:
                continue
            add(claim)
            if len(verified) == limit:
                break
    by_id: dict[str, Claim] = {}
    catalog: list[dict[str, Any]] = []
    for index, claim in enumerate(verified, 1):
        claim_id = f"C{index:03d}"
        by_id[claim_id] = claim
        catalog.append(
            {
                "id": claim_id,
                "claim": claim.claim,
                "claim_type": claim.claim_type,
                "component": claim.component,
                "evidence": [
                    {
                        "file": reference.file,
                        "start_line": reference.start_line,
                        "end_line": reference.end_line,
                        "symbol": reference.symbol,
                    }
                    for reference in claim.evidence[:1]
                ],
            }
        )
    return catalog, by_id


def _references(claims: list[Claim]) -> list[EvidenceReference]:
    result: list[EvidenceReference] = []
    seen: set[tuple[str, str, int, int, str | None]] = set()
    for claim in claims:
        for reference in claim.evidence:
            key = (
                reference.file,
                reference.source_hash,
                reference.start_line,
                reference.end_line,
                reference.symbol,
            )
            if key not in seen:
                seen.add(key)
                result.append(reference)
    return result


def _resolve_statement(
    value: Any,
    claims_by_id: dict[str, Claim],
) -> tuple[str, list[Claim]] | None:
    if not isinstance(value, dict):
        return None
    text = value.get("text")
    claim_ids = value.get("claim_ids")
    if not isinstance(text, str) or not text.strip():
        return None
    if not isinstance(claim_ids, list) or not claim_ids:
        return None
    if any(not isinstance(item, str) or item not in claims_by_id for item in claim_ids):
        return None
    claims = [claims_by_id[item] for item in dict.fromkeys(claim_ids)]
    statement = text.strip()
    if not _statement_supported(statement, claims):
        return None
    return statement, claims


def _statement_supported(text: str, claims: list[Claim]) -> bool:
    evidence_text = " ".join(
        value
        for claim in claims
        for value in (claim.component, claim.claim)
        if value
    )
    statement_terms = {
        token.casefold()
        for token in _SUPPORT_TOKEN.findall(text)
        if token.casefold() not in _GENERIC_SUPPORT_TERMS
    }
    evidence_terms = {
        token.casefold()
        for token in _SUPPORT_TOKEN.findall(evidence_text)
        if token.casefold() not in _GENERIC_SUPPORT_TERMS
    }
    if not statement_terms or not evidence_terms:
        return False
    shared_terms = statement_terms.intersection(evidence_terms)
    term_coverage = len(shared_terms) / len(statement_terms)
    statement_identifiers = {
        value.casefold() for value in _IDENTIFIER.findall(text)
    }
    evidence_identifiers = {
        value.casefold() for value in _IDENTIFIER.findall(evidence_text)
    }
    if statement_identifiers.intersection(evidence_identifiers):
        return True
    if len(shared_terms) >= 2 and term_coverage >= 0.15:
        return True

    def trigrams(value: str) -> set[str]:
        normalized = re.sub(r"[^0-9a-z가-힣]+", "", value.casefold())
        return {
            normalized[index : index + 3]
            for index in range(max(0, len(normalized) - 2))
        }

    statement_trigrams = trigrams(text)
    evidence_trigrams = trigrams(evidence_text)
    return bool(statement_trigrams) and (
        len(statement_trigrams.intersection(evidence_trigrams))
        / len(statement_trigrams)
        >= 0.18
    )


def _repository_facts(manifest: AnalysisManifest) -> dict[str, Any]:
    languages = Counter(item.language for item in manifest.files if item.language)
    build_systems = sorted(
        {
            item.artifact_type
            for item in manifest.dependencies
            if item.artifact_type and item.artifact_type.casefold() not in {"jar", "binary"}
        }
    )
    return {
        "name": manifest.display_name,
        "languages": [{"name": name, "files": count} for name, count in languages.most_common(12)],
        "build_systems": build_systems,
        "components": [
            {
                "name": item.display_name,
                "type": item.component_type,
                "entry_points": list(item.entry_points[:5]),
            }
            for item in manifest.components[:80]
        ],
        "declared_dependencies": sorted({item.name for item in manifest.dependencies})[:60],
    }


def _fallback_report(
    manifest: AnalysisManifest,
    claims_by_id: dict[str, Claim],
) -> KnowledgeItem:
    claims = list(claims_by_id.values())
    key_claims: list[Claim] = []
    seen_components: set[str] = set()
    for claim in claims:
        component = claim.component.casefold()
        if component in seen_components and len(key_claims) < 3:
            continue
        seen_components.add(component)
        key_claims.append(claim)
        if len(key_claims) == 5:
            break
    languages = Counter(item.language for item in manifest.files if item.language)
    language_text = ", ".join(name for name, _ in languages.most_common(5))
    if key_claims:
        summary = (
            f"{manifest.display_name}는 {len(manifest.components)}개 구성 영역과 "
            f"{len(manifest.dependencies)}개 선언 의존성을 가진 "
            f"{language_text or '소스'} 저장소입니다. 전체 목적은 모델 근거 종합이 "
            "완료되기 전까지 확정하지 않으며, 아래에는 원본 검증을 통과한 역할만 표시합니다."
        )
    else:
        summary = (
            f"{manifest.display_name} 저장소에서 {len(manifest.files)}개 파일을 확인했지만, "
            "전체 역할을 설명할 검증된 코드 Claim은 아직 없습니다."
        )
    detail_lines = [
        "## 저장소 구조",
        f"- 분석된 파일: {len(manifest.files)}개",
        f"- 구성 영역: {len(manifest.components)}개",
        f"- 선언 의존성: {len(manifest.dependencies)}개",
        "## 근거로 확인된 역할",
        *(
            [f"- {claim.claim}" for claim in key_claims]
            or ["- 전체 역할을 단정할 수 있는 검증 근거가 부족합니다."]
        ),
        "## 기술 구성",
        f"- 주 사용 언어: {language_text or '감지되지 않음'}",
        "- 라이브러리의 실제 런타임 역할은 관련 코드 Claim이 확인된 경우에만 확정합니다.",
        "## 처리 흐름과 생명주기",
        "- 아래 단계는 파일·심볼 이름으로 분류한 처리 영역이며 실제 실행 순서를 뜻하지 않습니다.",
        "## 분석 경계",
        "- 저장소 전체 목적과 단계 간 실행 순서는 모델 근거 종합 전까지 확정하지 않습니다.",
        "- 실행 시점의 외부 시스템 연결과 환경별 설정 값은 정적 분석만으로 확정하지 않습니다.",
    ]
    steps = [
        f"추정 처리 영역 — {node.title}: {node.description}"
        for node in sorted(manifest.lifecycle_nodes, key=lambda item: item.sequence)
    ]
    if len(steps) < 2:
        steps = [
            f"검증된 역할 — {claim.component}: {claim.claim}" for claim in key_claims[:5]
        ]
    references = _references(key_claims)
    manifest.metrics.update(
        {
            "repository_report_quality_gate": "LIMITED_FALLBACK",
            "repository_report_flow_steps": len(steps),
            "repository_report_source_references": len(references),
        }
    )
    return KnowledgeItem(
        knowledge_type="REPOSITORY_OVERVIEW",
        title=f"{manifest.display_name} 저장소 전체 보고서",
        summary=summary,
        detail="\n".join(detail_lines),
        processing_steps=steps,
        components=sorted({claim.component for claim in key_claims}),
        configurations=[],
        dependencies=sorted({item.name for item in manifest.dependencies})[:100],
        source_references=references[:100],
        validation_status=(
            ValidationStatus.PARTIALLY_VERIFIED
            if references
            else ValidationStatus.ADDITIONAL_DATA_NEEDED
        ),
        confidence=Confidence.MEDIUM if references else Confidence.LOW,
        unknowns=[
            "저장소 전체 목적과 실제 단계 실행 순서는 모델 근거 종합이 필요합니다.",
            "실행 환경에서만 결정되는 외부 연결과 설정 값",
        ],
        analysis_version=manifest.analysis_version,
        prompt_version=manifest.prompt_version,
    )


def _replace_lifecycle(
    manifest: AnalysisManifest,
    flow: list[tuple[dict[str, Any], str, list[Claim]]],
) -> None:
    if len(flow) < 2:
        return
    nodes: list[LifecycleNode] = []
    for index, (raw, description, claims) in enumerate(flow, 1):
        phase = str(raw.get("phase", "PROCESSING")).upper()
        if phase not in _PHASES:
            phase = "PROCESSING"
        title = str(raw.get("title") or phase.title()).strip()
        references = _references(claims)
        nodes.append(
            LifecycleNode(
                key=f"report-{index:02d}-{phase.casefold()}",
                phase=phase,
                title=title,
                description=description,
                component_key=claims[0].component,
                sequence=index,
                evidence=tuple(references[:20]),
                validation_status=ValidationStatus.PARTIALLY_VERIFIED,
                confidence=Confidence.MEDIUM,
            )
        )
    edges = [
        LifecycleEdge(
            source_key=source.key,
            target_key=target.key,
            relation_type="NEXT_PHASE",
            label="검증 Claim으로 구성한 다음 단계",
            provenance="EVIDENCE_SYNTHESIZED",
            evidence=tuple([*source.evidence[:2], *target.evidence[:2]]),
        )
        for source, target in zip(nodes, nodes[1:], strict=False)
    ]
    manifest.lifecycle_nodes = nodes
    manifest.lifecycle_edges = edges


def synthesize_repository_report(
    manifest: AnalysisManifest,
    provider: Any | None,
) -> KnowledgeItem:
    manifest.metrics["repository_report_contract"] = _REPORT_CONTRACT
    catalog, claims_by_id = _claim_catalog(manifest)
    manifest.metrics.update(
        {
            "repository_report_claim_catalog": len(catalog),
            "repository_report_claim_prompt_chars": len(
                json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
            ),
        }
    )
    fallback = _fallback_report(manifest, claims_by_id)
    synthesize = getattr(provider, "synthesize_report", None)
    if not catalog or not callable(synthesize):
        manifest.metrics["repository_report_mode"] = "DETERMINISTIC_FALLBACK"
        manifest.metrics["repository_report_model_failure"] = (
            "NO_VERIFIED_CLAIMS" if not catalog else "MODEL_PROVIDER_UNAVAILABLE"
        )
        return fallback

    try:
        invocation = synthesize(
            system=_REPORT_SYSTEM,
            context={
                "repository": _repository_facts(manifest),
                "claim_catalog": catalog,
            },
        )
        payload = invocation.payload
    except Exception as exc:
        manifest.warnings.append(f"repository_report:model_synthesis_failed:{type(exc).__name__}")
        manifest.metrics["repository_report_mode"] = "DETERMINISTIC_FALLBACK"
        manifest.metrics["repository_report_model_failure"] = type(exc).__name__
        return fallback

    purpose = _resolve_statement(payload.get("purpose"), claims_by_id)
    if purpose is None:
        manifest.warnings.append("repository_report:purpose_failed_evidence_gate")
        manifest.metrics["repository_report_mode"] = "DETERMINISTIC_FALLBACK"
        manifest.metrics["repository_report_model_failure"] = "PURPOSE_EVIDENCE_GATE"
        return fallback

    rejected = 0
    capabilities: list[tuple[str, list[Claim]]] = []
    for value in payload.get("capabilities", []):
        resolved = _resolve_statement(value, claims_by_id)
        if resolved is None:
            rejected += 1
        else:
            capabilities.append(resolved)

    technologies: list[tuple[dict[str, Any], str, list[Claim]]] = []
    for value in payload.get("technologies", []):
        if not isinstance(value, dict):
            rejected += 1
            continue
        resolved = _resolve_statement(
            {"text": value.get("role"), "claim_ids": value.get("claim_ids")},
            claims_by_id,
        )
        name = value.get("name")
        if resolved is None or not isinstance(name, str) or not name.strip():
            rejected += 1
        else:
            technologies.append((value, resolved[0], resolved[1]))

    flow: list[tuple[dict[str, Any], str, list[Claim]]] = []
    for value in payload.get("processing_flow", []):
        if not isinstance(value, dict):
            rejected += 1
            continue
        resolved = _resolve_statement(
            {"text": value.get("description"), "claim_ids": value.get("claim_ids")},
            claims_by_id,
        )
        if resolved is None:
            rejected += 1
        else:
            flow.append((value, resolved[0], resolved[1]))

    operational_notes: list[tuple[str, list[Claim]]] = []
    for value in payload.get("operational_notes", []):
        resolved = _resolve_statement(value, claims_by_id)
        if resolved is None:
            rejected += 1
        else:
            operational_notes.append(resolved)

    all_claims = [
        *purpose[1],
        *(claim for _, claims in capabilities for claim in claims),
        *(claim for _, _, claims in technologies for claim in claims),
        *(claim for _, _, claims in flow for claim in claims),
        *(claim for _, claims in operational_notes for claim in claims),
    ]
    detail_lines = ["## 핵심 기능"]
    detail_lines.extend(
        [f"- {text}" for text, _ in capabilities]
        or ["- 목적 설명 외에 별도 기능 묶음은 근거 게이트를 통과하지 못했습니다."]
    )
    detail_lines.append("## 주요 기술")
    detail_lines.extend(
        [f"- {raw['name']}: {role}" for raw, role, _ in technologies]
        or ["- 기술별 역할은 추가 근거가 필요합니다."]
    )
    detail_lines.append("## 운영 관점")
    detail_lines.extend(
        [f"- {text}" for text, _ in operational_notes]
        or ["- 실행 환경에서 확인할 운영 정보가 남아 있습니다."]
    )
    unknowns = [
        item.strip()
        for item in payload.get("unknowns", [])
        if isinstance(item, str) and item.strip()
    ]
    if unknowns:
        detail_lines.extend(["## 아직 확정할 수 없는 내용", *[f"- {item}" for item in unknowns]])

    _replace_lifecycle(manifest, flow)
    references = _references(all_claims)
    manifest.metrics.update(
        {
            "repository_report_mode": "MODEL_EVIDENCE_SYNTHESIS",
            "repository_report_quality_gate": "EVIDENCE_SYNTHESIZED",
            "repository_report_model_failure": None,
            "repository_report_evidence_rejected_statements": rejected,
            "repository_report_flow_steps": len(flow),
            "repository_report_source_references": len(references),
        }
    )
    return KnowledgeItem(
        knowledge_type="REPOSITORY_OVERVIEW",
        title=f"{manifest.display_name} 저장소 전체 보고서",
        summary=purpose[0],
        detail="\n".join(detail_lines),
        processing_steps=[
            f"{str(raw.get('title') or raw.get('phase') or '처리 단계').strip()}: {description}"
            for raw, description, _ in flow
        ],
        components=sorted({claim.component for claim in all_claims}),
        configurations=sorted(
            {
                str(value["key"]) if isinstance(value, dict) else str(value)
                for claim in all_claims
                for value in claim.related_configs
            }
        ),
        dependencies=sorted({item.name for item in manifest.dependencies})[:100],
        source_references=references[:100],
        validation_status=ValidationStatus.PARTIALLY_VERIFIED,
        confidence=Confidence.MEDIUM,
        unknowns=unknowns or ["실행 환경에서만 결정되는 외부 연결과 설정 값"],
        analysis_version=manifest.analysis_version,
        prompt_version=manifest.prompt_version,
    )
