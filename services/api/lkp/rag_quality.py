from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .schemas import Provenance, SearchRequest, SearchResult

POLICY_REVISION = "verifier-guided-rag-v1"
_TERM = re.compile(r"[\w.+#-]{2,}", re.UNICODE)
_FAILURE_MARKERS = {
    "error",
    "failed",
    "failure",
    "exception",
    "timeout",
    "crash",
    "incident",
    "recovery",
    "root cause",
    "오류",
    "실패",
    "예외",
    "장애",
    "복구",
    "원인",
    "해결",
}
_PERFORMANCE_MARKERS = {
    "cpu",
    "gpu",
    "latency",
    "memory",
    "slow",
    "thermal",
    "성능",
    "지연",
    "메모리",
    "느림",
    "온도",
}
_STOP_TERMS = {
    "about",
    "after",
    "before",
    "from",
    "into",
    "that",
    "the",
    "this",
    "with",
    "대한",
    "에서",
    "으로",
    "하는",
}


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    project: str | None
    preferred_tags: tuple[str, ...]
    intent: str
    inferred_project: bool = False


@dataclass(frozen=True, slots=True)
class RewardBreakdown:
    total: float
    lexical_coverage: float
    semantic: float
    scope: float
    evidence: float
    hard_gate_reason: str | None = None


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split())


def terms(value: str) -> set[str]:
    return {
        item
        for item in _TERM.findall(value.casefold())
        if item not in _STOP_TERMS
    }


def infer_query_scope(query: str, projects: Iterable[str]) -> RetrievalScope:
    normalized = _normalized(query)
    matches = [
        project
        for project in projects
        if project
        and re.search(
            rf"(?<![\w-]){re.escape(_normalized(project))}(?![\w-])",
            normalized,
        )
    ]
    maximal_matches = [
        project
        for project in matches
        if not any(
            project != other
            and _normalized(project) in _normalized(other)
            for other in matches
        )
    ]
    project = maximal_matches[0] if len(maximal_matches) == 1 else None
    failure = any(marker in normalized for marker in _FAILURE_MARKERS)
    performance = any(marker in normalized for marker in _PERFORMANCE_MARKERS)
    if performance:
        tags = ("case:performance",)
        intent = "performance"
    elif failure:
        tags = ("case:error_resolution", "case:operations")
        intent = "failure"
    else:
        tags = ()
        intent = "general"
    return RetrievalScope(
        project=project,
        preferred_tags=tags,
        intent=intent,
        inferred_project=project is not None,
    )


def candidate_limit(top_k: int) -> int:
    return min(200, max(30, top_k * 8))


def hard_gate_reason(
    result: SearchResult,
    request: SearchRequest,
    scope: RetrievalScope,
) -> str | None:
    expected_project = request.project or scope.project
    if expected_project and result.project != expected_project:
        return "project_scope_violation"
    relative_path = result.provenance.relative_path.replace("\\", "/")
    if request.path_prefix and not relative_path.casefold().startswith(
        request.path_prefix.replace("\\", "/").casefold()
    ):
        return "path_scope_violation"
    if request.tags:
        actual = set(result.tags)
        requested = set(request.tags)
        if request.tag_mode == "all" and not requested.issubset(actual):
            return "tag_scope_violation"
        if request.tag_mode == "any" and actual.isdisjoint(requested):
            return "tag_scope_violation"
    provenance = result.provenance
    if (
        provenance.start_line < 1
        or provenance.end_line < provenance.start_line
        or len(provenance.content_hash) != 64
    ):
        return "invalid_provenance"
    return None


def reward_result(
    result: SearchResult,
    request: SearchRequest,
    scope: RetrievalScope,
) -> RewardBreakdown:
    gate = hard_gate_reason(result, request, scope)
    if gate:
        return RewardBreakdown(0, 0, 0, 0, 0, gate)
    query_terms = terms(request.query)
    haystack = " ".join(
        [
            result.title,
            result.heading_or_symbol or "",
            result.snippet,
            result.provenance.relative_path,
        ]
    )
    matched = query_terms.intersection(terms(haystack))
    coverage = len(matched) / len(query_terms) if query_terms else 0.0
    semantic = max(0.0, result.vector_similarity or 0.0)
    scope_reward = 0.0
    expected_project = request.project or scope.project
    if expected_project and result.project == expected_project:
        scope_reward += 0.12
    preferred = set(scope.preferred_tags)
    if preferred.intersection(result.tags):
        scope_reward += 0.18
    evidence_reward = 0.08
    if "lifecycle:verified" in result.tags:
        evidence_reward += 0.08
    if any(tag.startswith("knowledge-value:") for tag in result.tags):
        evidence_reward += 0.04
    lexical_signal = min(1.0, max(0.0, (result.lexical_rank or 0.0) * 4))
    retrieval_signal = min(1.0, max(0.0, result.fused_rank * 61))
    total = (
        0.34 * coverage
        + 0.18 * lexical_signal
        + 0.20 * semantic
        + 0.08 * retrieval_signal
        + scope_reward
        + evidence_reward
    )
    return RewardBreakdown(
        total=round(total, 8),
        lexical_coverage=coverage,
        semantic=semantic,
        scope=scope_reward,
        evidence=evidence_reward,
    )


def _minimum_reward(result: SearchResult, scope: RetrievalScope) -> float:
    if result.vector_similarity is not None and result.vector_similarity >= 0.62:
        return 0.24
    if any(
        reason in result.match_reason
        for reason in ("exact symbol match", "path match")
    ):
        return 0.20
    if set(scope.preferred_tags).intersection(result.tags):
        return 0.31
    return 0.34


def reward_rerank(
    results: Sequence[SearchResult],
    request: SearchRequest,
    scope: RetrievalScope,
) -> list[SearchResult]:
    scored: list[tuple[float, SearchResult, RewardBreakdown]] = []
    for result in results:
        reward = reward_result(result, request, scope)
        if reward.hard_gate_reason or reward.total < _minimum_reward(result, scope):
            continue
        query_term_count = len(terms(request.query))
        has_strong_semantic = (
            result.vector_similarity is not None
            and result.vector_similarity >= 0.62
        )
        has_exact_locator = any(
            reason in result.match_reason
            for reason in ("exact symbol match", "path match")
        )
        has_preferred_case = bool(
            set(scope.preferred_tags).intersection(result.tags)
        )
        has_supported_case = has_preferred_case and reward.lexical_coverage >= 0.45
        if (
            query_term_count >= 4
            and reward.lexical_coverage < 0.6
            and not has_strong_semantic
            and not has_exact_locator
            and not has_supported_case
        ):
            continue
        scored.append((reward.total, result, reward))
    scored.sort(
        key=lambda item: (
            item[0],
            item[2].lexical_coverage,
            item[1].vector_similarity or -1,
            item[1].lexical_rank or -1,
        ),
        reverse=True,
    )
    selected: list[SearchResult] = []
    per_document: dict[str, int] = defaultdict(int)
    for _, result, reward in scored:
        document_id = str(result.provenance.document_id)
        if per_document[document_id] >= 2:
            continue
        result.match_reason = [
            *result.match_reason,
            f"reward-guided:{POLICY_REVISION}",
            f"query-coverage:{reward.lexical_coverage:.2f}",
        ]
        selected.append(result)
        per_document[document_id] += 1
        if len(selected) >= request.top_k:
            break
    return selected


_NEIGHBOR_SQL = text(
    """
    WITH anchor AS (
      SELECT document_version_id, chunk_index
      FROM document_chunk
      WHERE id = CAST(:chunk_id AS uuid)
    )
    SELECT d.id document_id, v.id version_id, c.id chunk_id, r.name source_root,
      d.canonical_path, d.relative_path, c.start_line, c.end_line, c.content,
      c.content_hash, v.detected_at, abs(c.chunk_index - a.chunk_index) distance
    FROM anchor a
    JOIN document_chunk c ON c.document_version_id = a.document_version_id
      AND c.chunk_index BETWEEN a.chunk_index - :radius AND a.chunk_index + :radius
    JOIN document_version v ON v.id = c.document_version_id
    JOIN document d ON d.current_version_id = v.id
    JOIN source_root r ON r.id = d.source_root_id
    WHERE d.state = 'active' AND r.data_scope = 'production'
    ORDER BY distance, c.chunk_index
    """
)


def build_expanded_context(
    session: Session,
    results: Sequence[SearchResult],
    *,
    max_chars: int,
    neighbor_radius: int = 1,
) -> list[dict[str, Any]]:
    contexts: list[dict[str, Any]] = []
    seen_chunks: set[str] = set()
    used = 0
    for result in results:
        rows = session.execute(
            _NEIGHBOR_SQL,
            {
                "chunk_id": str(result.provenance.chunk_id),
                "radius": neighbor_radius,
            },
        ).mappings()
        for row in rows:
            chunk_id = str(row["chunk_id"])
            if chunk_id in seen_chunks:
                continue
            remaining = max_chars - used
            if remaining <= 0:
                return contexts
            content = row["content"]
            if len(content) > remaining:
                if contexts:
                    continue
                content = content[:remaining]
            distance = int(row["distance"])
            contexts.append(
                {
                    "content": content,
                    "retrieval_score": result.fused_rank * math.pow(0.92, distance),
                    "provenance": Provenance(
                        document_id=row["document_id"],
                        document_version_id=row["version_id"],
                        chunk_id=row["chunk_id"],
                        source_root=row["source_root"],
                        canonical_path=row["canonical_path"],
                        relative_path=row["relative_path"],
                        start_line=row["start_line"],
                        end_line=row["end_line"],
                        content_hash=row["content_hash"],
                        indexed_timestamp=row["detected_at"].isoformat(),
                    ).model_dump(mode="json"),
                }
            )
            seen_chunks.add(chunk_id)
            used += len(content)
    return contexts


def verify_answer_candidate(
    candidate: dict[str, Any],
    contexts: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    evidence = {
        str(item["provenance"]["chunk_id"]): item["content"]
        for item in contexts
        if isinstance(item.get("provenance"), dict)
    }
    claims = candidate.get("claims")
    if not isinstance(claims, list) or not claims:
        return {"valid": False, "score": 0.0, "failures": ["claim_missing"]}
    failures: list[str] = []
    supported = 0
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict) or not isinstance(claim.get("text"), str):
            failures.append(f"claim_schema:{index}")
            continue
        citations = claim.get("citations")
        if not isinstance(citations, list) or not citations:
            failures.append(f"citation_missing:{index}")
            continue
        unknown = [citation for citation in citations if str(citation) not in evidence]
        if unknown:
            failures.append(f"citation_unknown:{index}")
            continue
        claim_terms = terms(claim["text"])
        cited_terms = terms(" ".join(evidence[str(citation)] for citation in citations))
        overlap = len(claim_terms.intersection(cited_terms)) / max(1, len(claim_terms))
        if overlap < 0.35:
            failures.append(f"claim_unsupported:{index}")
            continue
        supported += 1
    score = supported / len(claims)
    return {
        "valid": not failures,
        "score": score,
        "failures": failures,
        "policy": POLICY_REVISION,
    }


def select_verified_answer(
    candidates: Sequence[dict[str, Any]],
    contexts: Sequence[dict[str, Any]],
    *,
    repair: Callable[[dict[str, Any], list[str]], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    evaluated = [
        (verify_answer_candidate(candidate, contexts), candidate)
        for candidate in candidates
    ]
    valid = [item for item in evaluated if item[0]["valid"]]
    if valid:
        verification, candidate = max(valid, key=lambda item: item[0]["score"])
        return {
            "answer": candidate,
            "verification": verification,
            "repaired": False,
            "no_answer": False,
        }
    if repair is not None and evaluated:
        best_verification, best_candidate = max(
            evaluated,
            key=lambda item: item[0]["score"],
        )
        repaired = repair(best_candidate, list(best_verification["failures"]))
        if repaired is not None:
            verification = verify_answer_candidate(repaired, contexts)
            if verification["valid"]:
                return {
                    "answer": repaired,
                    "verification": verification,
                    "repaired": True,
                    "no_answer": False,
                }
    return {
        "answer": None,
        "verification": {
            "valid": False,
            "score": 0.0,
            "failures": ["no_verified_answer"],
            "policy": POLICY_REVISION,
        },
        "repaired": repair is not None,
        "no_answer": True,
    }
