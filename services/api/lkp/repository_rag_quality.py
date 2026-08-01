from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from .rag_quality import POLICY_REVISION, terms

_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.]{2,}\b")
_TYPE_MARKERS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        (
            "entry point",
            "startup",
            "architecture",
            "structure",
            "lifecycle",
            "시작",
            "구조",
            "아키텍처",
            "라이프사이클",
            "전체",
            "역할",
        ),
        ("REPOSITORY_OVERVIEW", "ARCHITECTURE", "COMPONENT_FLOW"),
    ),
    (
        ("processing flow", "request flow", "처리 흐름", "요청 처리", "흐름"),
        ("COMPONENT_FLOW", "LOGIC_FLOW", "DATA_FLOW"),
    ),
    (("configuration", "config", "설정"), ("CONFIGURATION", "COMPONENT")),
    (("retry", "timeout", "재시도", "타임아웃"), ("RETRY_TIMEOUT", "ERROR_HANDLING")),
    (("transaction", "rollback", "트랜잭션"), ("DATA_FLOW", "ERROR_HANDLING")),
    (("message", "queue", "메시지"), ("LOGIC_FLOW", "COMPONENT")),
    (("dependency", "jar", "의존"), ("DEPENDENCY", "COMPONENT")),
    (
        ("error", "failure", "cause", "오류", "장애", "원인"),
        ("ERROR_HANDLING", "RETRY_TIMEOUT", "COMPONENT"),
    ),
    (("change", "impact", "changing", "변경", "영향"), ("COMPONENT", "LOGIC_FLOW")),
    (
        ("declared", "declaration", "structure", "concurrency", "runtime behavior"),
        ("COMPONENT",),
    ),
    (
        (
            "confirmed facts",
            "hypotheses",
            "counter-evidence",
            "next verification step",
        ),
        ("ARCHITECTURE",),
    ),
)


def infer_repository_types(query: str) -> set[str]:
    normalized = query.casefold()
    inferred: set[str] = set()
    for markers, knowledge_types in _TYPE_MARKERS:
        if any(marker in normalized for marker in markers):
            inferred.update(knowledge_types)
    return inferred


def _valid_references(row: dict[str, Any]) -> bool:
    if row.get("_source_references_current") is False:
        return False
    references = row.get("source_references")
    if not isinstance(references, list) or not references:
        return False
    for reference in references:
        if not isinstance(reference, dict):
            return False
        if not isinstance(reference.get("file"), str) or not reference["file"]:
            return False
        if len(str(reference.get("source_hash") or "")) != 64:
            return False
        start = reference.get("start_line")
        end = reference.get("end_line")
        if not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            return False
    return True


def repository_reward(row: dict[str, Any], query: str) -> tuple[float, dict[str, float]]:
    if not _valid_references(row):
        return 0.0, {"hard_gate": 1.0}
    query_terms = terms(query)
    content = " ".join(
        str(value or "")
        for value in (
            row.get("title"),
            row.get("project"),
            row.get("summary"),
            row.get("detail"),
            row.get("processing_steps"),
            " ".join(row.get("components") or []),
            " ".join(row.get("configurations") or []),
            " ".join(row.get("dependencies") or []),
            " ".join(
                str(value or "")
                for reference in row.get("source_references") or []
                for value in (reference.get("file"), reference.get("symbol"))
            ),
        )
    )
    coverage = len(query_terms.intersection(terms(content))) / max(1, len(query_terms))
    identifiers = {
        value.casefold()
        for value in _IDENTIFIER.findall(query)
        if value.casefold() not in {"what", "where", "which", "when", "runtime"}
    }
    references = row["source_references"]
    reference_values = {
        str(value).casefold()
        for reference in references
        for value in (
            reference.get("file"),
            reference.get("symbol"),
        )
        if value
    }
    identifier_match = max(
        (
            1.0
            for identifier in identifiers
            if any(
                identifier == value
                or value.endswith(f".{identifier}")
                or identifier in value.rsplit("/", 1)[-1]
                for value in reference_values
            )
        ),
        default=0.0,
    )
    vector = max(0.0, float(row.get("vector_similarity") or 0.0))
    keyword = min(1.0, float(row.get("keyword_matches") or 0.0) / 4)
    type_match = float(row.get("knowledge_type") in infer_repository_types(query))
    validation = (
        1.0
        if row.get("validation_status")
        in {
            "SOURCE_VERIFIED",
            "TEST_VERIFIED",
            "RUNTIME_VERIFIED",
            "HUMAN_APPROVED",
        }
        else 0.5
    )
    score = (
        0.30 * coverage
        + 0.16 * vector
        + 0.10 * keyword
        + 0.25 * identifier_match
        + 0.11 * type_match
        + 0.08 * validation
    )
    return score, {
        "coverage": coverage,
        "vector": vector,
        "identifier": identifier_match,
        "type": type_match,
        "validation": validation,
    }


def repository_reward_rerank(
    rows: Sequence[dict[str, Any]],
    query: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    scored: list[tuple[float, dict[str, Any], dict[str, float]]] = []
    for row in rows:
        score, breakdown = repository_reward(row, query)
        strong_anchor = breakdown.get("identifier", 0) == 1
        type_anchor = (
            breakdown.get("type", 0) == 1
            and breakdown.get("validation", 0) == 1
            and breakdown.get("coverage", 0) >= 0.10
        )
        if score < 0.24 and not strong_anchor and not type_anchor:
            continue
        scored.append((score, row, breakdown))
    scored.sort(
        key=lambda item: (
            item[0],
            item[2].get("identifier", 0),
            item[2].get("coverage", 0),
            item[2].get("vector", 0),
        ),
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    per_file: dict[str, int] = defaultdict(int)
    for _, row, _ in scored:
        primary_file = str(row["source_references"][0]["file"])
        if per_file[primary_file] >= 2:
            continue
        selected.append(row)
        per_file[primary_file] += 1
        if len(selected) >= limit:
            break
    return selected


def repository_policy_metadata() -> dict[str, str]:
    return {"policy": POLICY_REVISION, "repository_policy": "source-reference-reward-v1"}
