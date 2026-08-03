"""Pure policy and selection helpers for progressive MCP retrieval."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from lkp.agent_evidence import (
    estimate_tokens,
    evidence_identity,
    evidence_text,
    exact_anchors,
)
from lkp.rag_quality import terms


@dataclass(frozen=True, slots=True)
class ProgressiveRetrievalConfig:
    enabled: bool = False
    source_first: bool = False
    compact_cards: bool = False
    session_dedup: bool = False
    low_confidence_navigation: bool = False
    query_focused_compression: bool = False
    mmr_enabled: bool = False
    conflict_gate: bool = False
    mmr_lambda: float = 0.72
    evidence_token_budget: int = 1_200
    early_stop_score: float = 0.72

    @classmethod
    def from_settings(cls, settings) -> ProgressiveRetrievalConfig:
        return cls(
            enabled=settings.mcp_progressive_enabled,
            source_first=settings.mcp_source_first_enabled,
            compact_cards=settings.mcp_compact_evidence_cards_enabled,
            session_dedup=settings.mcp_session_dedup_enabled,
            low_confidence_navigation=settings.mcp_low_confidence_navigation_enabled,
            query_focused_compression=settings.mcp_query_focused_compression_enabled,
            mmr_enabled=settings.mcp_mmr_enabled,
            conflict_gate=settings.mcp_conflict_gate_enabled,
            mmr_lambda=settings.mcp_mmr_lambda,
            evidence_token_budget=settings.mcp_evidence_token_budget,
            early_stop_score=settings.mcp_early_stop_score,
        )


_PATH_OR_SYMBOL = re.compile(
    r"(?:^|\s)(?:[\w.-]+/)+[\w.-]+|\b[A-Za-z_][A-Za-z0-9_]{3,}\s*(?:\(|::|\.)"
)


def retrieval_is_authorized(arguments: dict[str, Any], purpose: str, *, source_first: bool) -> bool:
    if not source_first:
        return True
    trigger = arguments.get("trigger")
    if trigger in {"explicit_request", "prior_decision", "incident", "experiment"}:
        return True
    return trigger == "source_miss" and arguments.get("source_search_status") == "miss"


def exact_repository_map_is_useful(query: str) -> bool:
    return bool(_PATH_OR_SYMBOL.search(query))


def _strength(item: dict[str, Any]) -> float:
    return {"verified": 1.0, "source": 0.9}.get(str(item.get("evidence_level")), 0.0)


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left.union(right)
    return len(left.intersection(right)) / len(union) if union else 0.0


def token_aware_select(
    contexts: list[dict[str, Any]],
    *,
    query: str,
    top_k: int,
    token_budget: int,
    seen_evidence: set[str] | None = None,
    mmr_lambda: float = 1.0,
    preserve_exact_anchors: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int | float]]:
    query_terms = terms(query)
    query_anchors = exact_anchors(query)
    seen = seen_evidence or set()
    candidates: list[dict[str, Any]] = []
    duplicates = 0
    for item in contexts:
        content = evidence_text(item)
        identity = evidence_identity(item)
        if not identity or not content:
            continue
        if identity in seen:
            duplicates += 1
            continue
        content_terms = terms(" ".join([str(item.get("title") or ""), content]))
        relevance = len(query_terms.intersection(content_terms)) / max(1, len(query_terms))
        content_anchors = exact_anchors(content)
        value_anchors = content_anchors.difference(query_anchors)
        anchor_coverage = len(query_anchors.intersection(content_anchors)) / max(
            1, len(query_anchors)
        )
        strength = _strength(item)
        base = max(0.0, min(1.0, float(item.get("selection_score") or 0.0)))
        cost = estimate_tokens(content)
        utility = (
            0.35 * relevance
            + 0.25 * strength
            + 0.15 * base
            + 0.10
            + 0.15 * anchor_coverage
        )
        # Raw utility-per-token over-rewards tiny distractors. A logarithmic
        # penalty preserves token awareness without allowing length to erase
        # exact identifiers or strong evidence.
        quality = utility / (1 + 0.12 * math.log2(max(1, cost)))
        candidates.append(
            {
                "item": item,
                "identity": identity,
                "cost": cost,
                "terms": content_terms,
                "quality": quality,
                "anchor_coverage": anchor_coverage,
                "value_anchors": value_anchors,
            }
        )

    effective_lambda = max(0.0, min(1.0, mmr_lambda))
    selected: list[dict[str, Any]] = []
    selected_features: list[dict[str, Any]] = []
    used = 0
    redundant_suppressed = 0
    remaining = list(candidates)
    while remaining and len(selected) < top_k:
        scored: list[tuple[float, float, float, dict[str, Any]]] = []
        for candidate in remaining:
            redundancy = max(
                (
                    0.0
                    if candidate["value_anchors"]
                    and chosen["value_anchors"]
                    and candidate["value_anchors"] != chosen["value_anchors"]
                    else _jaccard(candidate["terms"], chosen["terms"])
                    for chosen in selected_features
                ),
                default=0.0,
            )
            score = effective_lambda * candidate["quality"] - (
                1 - effective_lambda
            ) * redundancy
            if (
                preserve_exact_anchors
                and query_anchors
                and candidate["anchor_coverage"] == 1.0
            ):
                score += 0.05
            scored.append(
                (
                    score,
                    candidate["anchor_coverage"],
                    -candidate["cost"],
                    candidate,
                )
            )
        _score, _anchor, _cost_key, chosen = max(scored, key=lambda value: value[:3])
        remaining.remove(chosen)
        redundancy = max(
            (
                0.0
                if chosen["value_anchors"]
                and item["value_anchors"]
                and chosen["value_anchors"] != item["value_anchors"]
                else _jaccard(chosen["terms"], item["terms"])
                for item in selected_features
            ),
            default=0.0,
        )
        if selected and effective_lambda < 1.0 and redundancy >= 0.92:
            redundant_suppressed += 1
            continue
        if used + chosen["cost"] > token_budget:
            continue
        selected.append(chosen["item"])
        selected_features.append(chosen)
        used += chosen["cost"]
    return selected, {
        "candidate_count": len(candidates),
        "selected_tokens": used,
        "deduplicated_count": duplicates,
        "redundant_suppressed_count": redundant_suppressed,
        "exact_anchor_retained": int(
            bool(query_anchors)
            and any(item["anchor_coverage"] == 1.0 for item in selected_features)
        ),
        "mmr_lambda": round(effective_lambda, 4),
        "best_selection_score": max(
            (float(item.get("selection_score") or 0.0) for item in selected),
            default=0.0,
        ),
    }
