from __future__ import annotations

import time
import uuid
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from .domain import (
    AnalysisManifest,
    EvaluationCase,
    EvaluationResult,
    EvidenceReference,
    KnowledgeItem,
)

if TYPE_CHECKING:
    from .provider import LocalModelProvider

_EVALUATION_NAMESPACE = uuid.UUID("1567eef0-95b8-4681-9cbd-4087ad8ce512")
_SUPPORT_QUESTION_SPECS = (
    ("ENTRY_POINT", "기능 진입점과 최초 실행 조건은 무엇인가?"),
    ("CALL_FLOW", "주요 호출 흐름과 각 단계의 역할은 무엇인가?"),
    ("IMPLEMENTATION_SELECTION", "실제 구현체는 어디에서 어떤 조건으로 선택되는가?"),
    ("CONFIG_PRIORITY", "관련 설정의 위치와 적용 우선순위는 무엇인가?"),
    ("EXCEPTION_CONDITION", "예외가 발생하는 조건과 처리 경로는 무엇인가?"),
    ("RETRY_TIMEOUT", "재시도와 타임아웃 조건, 횟수, 종료 조건은 무엇인가?"),
    ("TRANSACTION", "트랜잭션 경계와 실패 시 롤백 범위는 무엇인가?"),
    ("DB_MESSAGE_FLOW", "DB·메시지의 입력, 처리, 출력 흐름은 무엇인가?"),
    ("CONCURRENCY_STATE", "동시성 제어와 상태 변화는 어떻게 이루어지는가?"),
    ("DEPENDENCY_USAGE", "의존성은 실제로 어디에서 어떤 기능에 사용되는가?"),
    ("JAR_MISSING_IMPACT", "관련 JAR가 누락되거나 변경되면 어떤 영향이 예상되는가?"),
    ("LOG_LOCATION", "장애를 확인할 로그 발생 위치와 관련 코드 근거는 어디인가?"),
    ("FAILURE_CANDIDATES", "장애 후보 원인과 각 후보를 지지하거나 반박하는 근거는 무엇인가?"),
    ("ADDITIONAL_EVIDENCE", "현재 결론에 추가로 필요한 운영 자료와 확인 방법은 무엇인가?"),
    ("CHANGE_IMPACT", "이 기능을 변경할 때 영향을 받는 흐름·설정·의존성은 무엇인가?"),
)
_QUESTION_TERMS = {
    "ENTRY_POINT": ("진입", "startup", "entry", "main"),
    "CALL_FLOW": ("호출", "흐름", "flow", "처리"),
    "IMPLEMENTATION_SELECTION": ("구현", "선택", "factory", "binding", "adapter"),
    "CONFIG_PRIORITY": ("설정", "config", "override", "우선"),
    "EXCEPTION_CONDITION": ("예외", "exception", "오류", "실패"),
    "RETRY_TIMEOUT": ("retry", "재시도", "timeout", "타임아웃"),
    "TRANSACTION": ("transaction", "트랜잭션", "rollback", "롤백"),
    "DB_MESSAGE_FLOW": ("db", "sql", "메시지", "queue", "topic"),
    "CONCURRENCY_STATE": ("동시", "thread", "상태", "state", "lock"),
    "DEPENDENCY_USAGE": ("의존", "dependency", "jar", "라이브러리"),
    "JAR_MISSING_IMPACT": ("jar", "dependency", "의존", "누락"),
    "LOG_LOCATION": ("로그", "log", "exception", "예외"),
    "FAILURE_CANDIDATES": ("장애", "실패", "오류", "예외"),
    "ADDITIONAL_EVIDENCE": ("확인", "unknown", "운영", "추가"),
    "CHANGE_IMPACT": ("변경", "영향", "호출", "의존"),
}
_ANSWER_ARRAY_FIELDS = (
    "confirmed_facts",
    "hypotheses",
    "counter_evidence",
    "source_references",
    "configurations",
    "additional_data",
    "next_steps",
)


def _fallback_evidence(manifest: AnalysisManifest) -> list[EvidenceReference]:
    if not manifest.symbols:
        return []
    symbol = sorted(
        manifest.symbols,
        key=lambda item: (item.relative_path, item.start_line, item.symbol),
    )[0]
    source = next(
        (item for item in manifest.files if item.relative_path == symbol.relative_path),
        None,
    )
    if source is None:
        return []
    return [
        EvidenceReference(
            file=symbol.relative_path,
            source_hash=source.content_hash,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            symbol=symbol.symbol,
        )
    ]


def _retrieval_anchor(
    manifest: AnalysisManifest,
    question_type: str,
) -> tuple[KnowledgeItem | None, EvidenceReference | None]:
    terms = _QUESTION_TERMS[question_type]
    ranked: list[tuple[int, KnowledgeItem]] = []
    for item in manifest.knowledge_items:
        if not item.searchable or not item.source_references:
            continue
        haystack = " ".join(
            [
                item.knowledge_type,
                item.title,
                item.summary,
                item.detail,
                *item.processing_steps,
                *item.configurations,
                *item.dependencies,
                *item.unknowns,
            ]
        ).casefold()
        score = sum(term in haystack for term in terms)
        if score:
            ranked.append((score, item))
    if not ranked:
        return None, None
    ranked.sort(key=lambda value: (value[0], value[1].title), reverse=True)
    selected = ranked[0][1]
    return selected, selected.source_references[0]


def build_evaluation_cases(manifest: AnalysisManifest) -> list[EvaluationCase]:
    """Build the required 15 support categories against this exact snapshot."""

    fallback = _fallback_evidence(manifest)
    if not fallback:
        return []
    cases: list[EvaluationCase] = []
    for question_type, prompt in _SUPPORT_QUESTION_SPECS:
        selected, matched_anchor = _retrieval_anchor(manifest, question_type)
        retrieval_answerable = matched_anchor is not None
        anchor = matched_anchor or fallback[0]
        component = (
            selected.components[0]
            if selected is not None and selected.components
            else anchor.symbol or manifest.display_name
        )
        question = f"[{component}] {prompt}"
        cases.append(
            EvaluationCase(
                id=uuid.uuid5(
                    _EVALUATION_NAMESPACE,
                    f"{manifest.snapshot_id}:{question_type}:{question}",
                ),
                question=question,
                question_type=question_type,
                expected_evidence=[anchor],
                required_files=[anchor.file],
                acceptable_answer=(
                    "확인된 사실·추론·반대 근거를 분리하고 실제 파일·symbol·line과 "
                    "설정, 추가 자료, 다음 검증 절차, confidence를 포함한다."
                ),
                forbidden_assertions=[
                    "근거 없는 단일 원인 확정",
                    "운영 증거 없는 runtime 사실 확정",
                    "제공되지 않은 파일·symbol·설정 인용",
                ],
                grading_criteria={
                    "answer_schema_valid": True,
                    "confirmed_fact_present": True,
                    "citation_valid": True,
                    "next_verification_step_present": True,
                    "snapshot_scoped": True,
                    "retrieval_answerable": retrieval_answerable,
                },
                difficulty="HARD",
                scenario_type=question_type.casefold(),
            )
        )
    return cases


def evaluate_evidence_integrity(
    manifest: AnalysisManifest,
    cases: list[EvaluationCase],
) -> list[EvaluationResult]:
    files = {item.relative_path: item for item in manifest.files}
    symbols = {(item.relative_path, item.symbol) for item in manifest.symbols}
    results: list[EvaluationResult] = []
    for case in cases:
        started = time.perf_counter()
        failures = _validate_references(
            case.expected_evidence,
            files=files,
            symbols=symbols,
        )
        for required in case.required_files:
            if required not in files:
                failures.append(f"missing_file:{required}")
        results.append(
            EvaluationResult(
                case_id=case.id,
                passed=not failures,
                score=1.0 if not failures else 0.0,
                failure_category=(
                    None if not failures else "EVIDENCE_VALIDATION_FAILURE"
                ),
                details={
                    "failures": failures,
                    "scope": "evidence_integrity_only",
                    "answer_quality_evaluated": False,
                },
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        )
    return results


def _validate_references(
    references: list[EvidenceReference],
    *,
    files: dict[str, Any],
    symbols: set[tuple[str, str]],
) -> list[str]:
    failures: list[str] = []
    for evidence in references:
        source = files.get(evidence.file)
        if source is None:
            failures.append(f"missing_file:{evidence.file}")
            continue
        if source.content_hash != evidence.source_hash:
            failures.append(f"source_hash_mismatch:{evidence.file}")
        if evidence.start_line < 1 or evidence.end_line > source.line_count:
            failures.append(f"invalid_line_range:{evidence.file}")
        if evidence.symbol and (evidence.file, evidence.symbol) not in symbols:
            failures.append(f"missing_symbol:{evidence.symbol}")
    return failures


def _rank_knowledge(
    case: EvaluationCase,
    items: list[KnowledgeItem],
    *,
    limit: int,
) -> list[KnowledgeItem]:
    terms = _QUESTION_TERMS[case.question_type]

    def score(item: KnowledgeItem) -> tuple[int, str]:
        haystack = " ".join(
            [
                item.knowledge_type,
                item.title,
                item.summary,
                item.detail,
                *item.processing_steps,
                *item.configurations,
                *item.dependencies,
                *item.unknowns,
            ]
        ).casefold()
        return (sum(term in haystack for term in terms), item.title)

    return sorted(
        (item for item in items if item.searchable),
        key=score,
        reverse=True,
    )[:limit]


def _knowledge_context(items: list[KnowledgeItem]) -> list[dict[str, Any]]:
    return [
        {
            "knowledge_type": item.knowledge_type,
            "title": item.title,
            "summary": item.summary,
            "detail": item.detail,
            "processing_steps": item.processing_steps,
            "components": item.components,
            "configurations": item.configurations,
            "dependencies": item.dependencies,
            "unknowns": item.unknowns,
            "validation_status": item.validation_status.value,
            "confidence": item.confidence.value,
            "source_references": [
                asdict(reference) for reference in item.source_references
            ],
        }
        for item in items
    ]


def _parse_answer_references(value: Any) -> list[EvidenceReference]:
    if not isinstance(value, list):
        raise ValueError("source_references must be an array")
    return [
        EvidenceReference(
            file=str(item["file"]),
            source_hash=str(item["source_hash"]),
            start_line=int(item["start_line"]),
            end_line=int(item["end_line"]),
            symbol=str(item["symbol"]) if item.get("symbol") else None,
        )
        for item in value
        if isinstance(item, dict)
    ]


def evaluate_support_answers(
    manifest: AnalysisManifest,
    cases: list[EvaluationCase],
    provider: LocalModelProvider,
) -> tuple[list[EvaluationResult], dict[str, Any]]:
    """Ask Qwen each required support question and verify its answer citations."""

    files = {item.relative_path: item for item in manifest.files}
    symbols = {(item.relative_path, item.symbol) for item in manifest.symbols}
    allowed_references = {
        (
            reference.file,
            reference.source_hash,
            reference.start_line,
            reference.end_line,
            reference.symbol,
        )
        for item in manifest.knowledge_items
        if item.searchable
        for reference in item.source_references
    }
    results: list[EvaluationResult] = []
    metrics: dict[str, Any] = {
        "requests": 0,
        "retries": 0,
        "latency_ms": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "failure_counts": {},
    }
    for case in cases:
        started = time.perf_counter()
        retrieved = _rank_knowledge(case, manifest.knowledge_items, limit=5)
        if not retrieved:
            results.append(
                EvaluationResult(
                    case_id=case.id,
                    passed=False,
                    score=0.0,
                    failure_category="RETRIEVAL_FAILURE",
                    details={
                        "answer_quality_evaluated": True,
                        "failures": ["no_searchable_knowledge"],
                    },
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
            )
            continue

        answer: dict[str, Any] | None = None
        invocation_error: str | None = None
        for attempt in range(2):
            try:
                invocation = provider.analyze(
                    system=(
                        "당신은 로컬 기술지원 지식 평가자다. 제공된 단일 프로젝트·Snapshot의 "
                        "검증된 지식만 사용해 질문에 답하라. JSON 객체만 반환하고 "
                        "confirmed_facts, hypotheses, counter_evidence, source_references, "
                        "configurations, additional_data, next_steps 배열과 confidence를 포함하라. "
                        "근거가 부족하면 추측하지 말고 additional_data에 기록하라. "
                        "source_references는 제공된 값을 그대로 사용하라."
                    ),
                    context={
                        "project_id": str(manifest.project_id),
                        "snapshot_id": str(manifest.snapshot_id),
                        "question_type": case.question_type,
                        "question": case.question,
                        "knowledge": _knowledge_context(
                            retrieved if attempt == 0 else retrieved[:3]
                        ),
                        "retry_instruction": (
                            None
                            if attempt == 0
                            else "스키마를 정확히 지키고 근거가 있는 최소 답변만 반환하라."
                        ),
                    },
                )
                metrics["requests"] += 1
                metrics["latency_ms"] += invocation.latency_ms
                metrics["prompt_tokens"] += invocation.prompt_tokens or 0
                metrics["completion_tokens"] += invocation.completion_tokens or 0
                if not all(
                    isinstance(invocation.payload.get(field), list)
                    for field in _ANSWER_ARRAY_FIELDS
                ):
                    raise ValueError("required answer arrays are missing")
                if invocation.payload.get("confidence") not in {"HIGH", "MEDIUM", "LOW"}:
                    raise ValueError("invalid confidence")
                answer = invocation.payload
                break
            except Exception as exc:
                invocation_error = type(exc).__name__
                if attempt == 0:
                    metrics["retries"] += 1

        if answer is None:
            failure_category = "MODEL_REASONING_FAILURE"
            details = {
                "answer_quality_evaluated": True,
                "failures": [f"model_answer_failure:{invocation_error}"],
                "retrieved_items": [item.title for item in retrieved],
            }
            score = 0.0
            passed = False
        else:
            failures: list[str] = []
            try:
                references = _parse_answer_references(answer["source_references"])
            except (KeyError, TypeError, ValueError):
                references = []
                failures.append("invalid_source_reference_schema")
            if not answer["confirmed_facts"]:
                failures.append("confirmed_fact_missing")
            if not references:
                failures.append("source_reference_missing")
            if not answer["next_steps"]:
                failures.append("next_verification_step_missing")
            failures.extend(
                _validate_references(references, files=files, symbols=symbols)
            )
            for reference in references:
                key = (
                    reference.file,
                    reference.source_hash,
                    reference.start_line,
                    reference.end_line,
                    reference.symbol,
                )
                if key not in allowed_references:
                    failures.append(f"unretrieved_or_unverified_reference:{reference.file}")
            score = max(0.0, 1.0 - min(len(set(failures)), 4) * 0.25)
            passed = not failures
            failure_category = (
                None
                if passed
                else (
                    "EVIDENCE_VALIDATION_FAILURE"
                    if any(
                        value.startswith(
                            (
                                "missing_",
                                "source_",
                                "invalid_",
                                "unretrieved_",
                            )
                        )
                        for value in failures
                    )
                    else "INSUFFICIENT_EVIDENCE"
                )
            )
            details = {
                "answer_quality_evaluated": True,
                "retrieval_scope": "validated_manifest_knowledge",
                "retrieved_items": [item.title for item in retrieved],
                "failures": sorted(set(failures)),
                "answer": answer,
            }
        if failure_category:
            counts = metrics["failure_counts"]
            counts[failure_category] = counts.get(failure_category, 0) + 1
        results.append(
            EvaluationResult(
                case_id=case.id,
                passed=passed,
                score=score,
                failure_category=failure_category,
                details=details,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        )
    return results, metrics
