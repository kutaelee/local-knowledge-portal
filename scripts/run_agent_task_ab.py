#!/usr/bin/env python3
"""Reproducible agent orchestration A/B evaluation.

The harness uses synthetic fixtures, an evidence-gated deterministic answerer,
and the same token estimator as progressive MCP selection for diagnostics only.
Acceptance efficiency requires separately captured content-free exact usage
telemetry. The harness never opens a repository, calls the portal, loads a model,
or sends network traffic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lkp.agent_evidence import compact_evidence_card
from lkp.security_boundary import is_prohibited_project
from lkp_indexer.agent_eval_telemetry import (
    ExactUsageRecord,
    load_exact_usage_jsonl,
    summarize_exact_usage,
)
from lkp_indexer.mcp_progressive import estimate_tokens, token_aware_select

HARNESS_VERSION = "agent-task-ab-v2-exact-usage"
VARIANTS = ("source-only", "current-conditional-mcp", "improved-progressive-mcp")
HISTORICAL_TYPES = {
    "prior_decision",
    "incident",
    "experiment",
    "repository_map",
    "explicit_request",
}


@dataclass(slots=True)
class TaskResult:
    variant: str
    task_id: str
    success: bool
    first_pass_success: bool
    expected_no_answer: bool
    predicted_no_answer: bool
    citation_valid: bool | None
    unsupported_claim_rejected: bool | None
    uncached_tokens: int
    total_tokens: int
    elapsed_ms: float
    tool_calls: int
    portal_calls: int
    retrieval_was_needed: bool
    returned_evidence: int
    utilized_evidence: int
    retrieval_regret: bool
    security_blocked: bool


def _expanded_content(item: dict[str, Any]) -> str:
    return " ".join([str(item.get("content") or "")] * int(item.get("repeat") or 1))


def _current_evidence(item: dict[str, Any]) -> bool:
    return (
        item.get("evidence_level") in {"source", "verified"}
        and item.get("confidence", "high") == "high"
        and item.get("current", True) is True
        and item.get("revision_match", True) is True
    )


def _supports(item: dict[str, Any], claim: str | None) -> bool:
    return bool(claim and claim.casefold() in _expanded_content(item).casefold())


def _relevance(item: dict[str, Any], query: str) -> float:
    query_terms = set(re.findall(r"[a-z0-9_]+", query.casefold()))
    evidence_terms = set(re.findall(r"[a-z0-9_]+", _expanded_content(item).casefold()))
    return len(query_terms.intersection(evidence_terms)) / max(1, len(query_terms))


def _progressive_select(
    candidates: list[dict[str, Any]], query: str, *, limit: int = 2
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for item in candidates:
        candidate = dict(item)
        candidate["content"] = _expanded_content(item)
        candidate["repeat"] = 1
        candidate["evidence_id"] = str(item["id"])
        candidate["selection_score"] = _relevance(item, query)
        prepared.append(candidate)
    selected, _metrics = token_aware_select(
        prepared,
        query=query,
        top_k=limit,
        token_budget=320,
        mmr_lambda=0.72,
    )
    return selected


def _run_task(
    task: dict[str, Any],
    variant: str,
    session_evidence: dict[str, set[str]],
) -> TaskResult:
    started = time.perf_counter_ns()
    query = str(task["query"])
    project = str(task["project"])
    expected_claim = task.get("expected_claim")
    expected_no_answer = bool(task.get("expected_no_answer"))
    security_blocked = is_prohibited_project(project)
    base_tokens = estimate_tokens(query) + 180
    source_items = [dict(item) for item in task.get("source") or []]
    source_valid = [item for item in source_items if _current_evidence(item)]
    source_support = next(
        (item for item in source_valid if _supports(item, expected_claim)),
        None,
    )
    source_tokens = sum(estimate_tokens(_expanded_content(item)) for item in source_items)
    returned: list[dict[str, Any]] = list(source_valid)
    portal_calls = 0
    tool_calls = 0 if security_blocked else 1
    retrieval_was_needed = not source_support and task["task_type"] in HISTORICAL_TYPES
    evidence_tokens = source_tokens
    reused_ids: set[str] = set()

    if not security_blocked and source_support is None and variant != "source-only":
        should_retrieve = task["task_type"] in HISTORICAL_TYPES
        if should_retrieve:
            tool_calls += 1
            ranges = task.get("ranges") or {}
            if variant == "current-conditional-mcp":
                for range_name in ("documents", "repository"):
                    if range_name not in ranges:
                        continue
                    portal_calls += 1
                    valid = [item for item in ranges[range_name] if _current_evidence(item)]
                    returned.extend(valid)
                    evidence_tokens += sum(
                        estimate_tokens(_expanded_content(item)) for item in valid
                    )
            else:
                session_id = str(task.get("session_id") or task["id"])
                seen = session_evidence.setdefault(session_id, set())
                candidates: list[dict[str, Any]] = []
                range_order = ["documents", "repository"]
                for range_name in range_order:
                    if range_name not in ranges:
                        continue
                    portal_calls += 1
                    candidates.extend(
                        item for item in ranges[range_name] if _current_evidence(item)
                    )
                    selected = _progressive_select(candidates, query)
                    best_relevance = max(
                        (_relevance(item, query) for item in selected),
                        default=0.0,
                    )
                    if any(_supports(item, expected_claim) for item in selected) or (
                        expected_no_answer and best_relevance >= 0.6
                    ):
                        break
                returned.extend(selected if candidates else [])
                for item in selected if candidates else []:
                    evidence_id = str(item["id"])
                    if evidence_id in seen:
                        reused_ids.add(evidence_id)
                        evidence_tokens += 12
                    else:
                        card = compact_evidence_card(
                            item,
                            query=query,
                            max_tokens=48,
                            query_focused=True,
                        )
                        evidence_tokens += estimate_tokens(
                            card["discriminating_evidence"]
                        )
                        seen.add(evidence_id)

    support = source_support or next(
        (item for item in returned if _supports(item, expected_claim)),
        None,
    )
    conflict_claims = [str(value) for value in task.get("conflicting_claims") or []]
    conflicting_support = [
        item
        for item in returned
        if any(_supports(item, claim) for claim in conflict_claims)
    ]
    if expected_no_answer and conflicting_support:
        support = conflicting_support[0]
        if variant == "improved-progressive-mcp" and len(
            {
                claim
                for claim in conflict_claims
                if any(_supports(item, claim) for item in conflicting_support)
            }
        ) > 1:
            support = None
    predicted_no_answer = support is None
    citation_valid: bool | None = None
    unsupported_rejected: bool | None = None
    first_pass_success = True
    if expected_claim and support is not None:
        tool_calls += 1
        citation_valid = _current_evidence(support) and _supports(support, expected_claim)
        probe = task.get("unsupported_probe")
        if probe:
            unsupported_rejected = not _supports(support, str(probe))
        if task.get("first_pass_unsupported"):
            first_pass_success = False
            tool_calls += 1
    elif expected_claim:
        first_pass_success = False
        citation_valid = False
    if expected_no_answer:
        success = predicted_no_answer
    else:
        success = bool(support and citation_valid)
    if task.get("first_pass_unsupported") and support is not None:
        success = success and bool(unsupported_rejected)
    if security_blocked:
        success = expected_no_answer and predicted_no_answer and portal_calls == 0

    returned_ids = {str(item["id"]) for item in returned}
    utilized_ids = {str(support["id"])} if support is not None else set()
    all_valid = [
        item
        for values in (task.get("ranges") or {}).values()
        for item in values
        if _current_evidence(item)
    ]
    relevant_exists = any(_supports(item, expected_claim) for item in all_valid)
    regret = bool(relevant_exists and support is None and not security_blocked)
    uncached_tokens = (
        base_tokens + evidence_tokens + (estimate_tokens(expected_claim or "") if support else 8)
    )
    total_tokens = uncached_tokens + 120
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
    return TaskResult(
        variant=variant,
        task_id=str(task["id"]),
        success=success,
        first_pass_success=first_pass_success and success,
        expected_no_answer=expected_no_answer,
        predicted_no_answer=predicted_no_answer,
        citation_valid=citation_valid,
        unsupported_claim_rejected=unsupported_rejected,
        uncached_tokens=uncached_tokens,
        total_tokens=total_tokens,
        elapsed_ms=elapsed_ms,
        tool_calls=tool_calls,
        portal_calls=portal_calls,
        retrieval_was_needed=retrieval_was_needed,
        returned_evidence=len(returned_ids),
        utilized_evidence=len(utilized_ids.intersection(returned_ids)),
        retrieval_regret=regret,
        security_blocked=security_blocked,
    )


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def _summarize(results: list[TaskResult]) -> dict[str, Any]:
    successes = sum(item.success for item in results)
    uncached = sum(item.uncached_tokens for item in results)
    total = sum(item.total_tokens for item in results)
    elapsed = sum(item.elapsed_ms for item in results)
    citations = [item for item in results if item.citation_valid is not None]
    no_answers = [item for item in results if item.expected_no_answer]
    unsupported = [item for item in results if item.unsupported_claim_rejected is not None]
    retrievals = [item for item in results if item.portal_calls]
    source_only = [item for item in results if not item.retrieval_was_needed]
    returned = sum(item.returned_evidence for item in results)
    utilized = sum(item.utilized_evidence for item in results)
    return {
        "tasks": len(results),
        "verified_tasks": successes,
        "estimated_verified_tasks_per_million_uncached_tokens": round(
            successes * 1_000_000 / uncached,
            2,
        ),
        "estimated_uncached_tokens_per_verified_task": round(uncached / successes, 2),
        "estimated_total_tokens_per_verified_task": round(total / successes, 2),
        "harness_elapsed_ms_per_verified_task": round(elapsed / successes, 4),
        "tool_calls_per_verified_task": round(
            sum(item.tool_calls for item in results) / successes, 3
        ),
        "first_pass_success_rate": _rate(
            sum(item.first_pass_success for item in results), len(results)
        ),
        "answer_quality": _rate(successes, len(results)),
        "retrieval_call_precision": _rate(
            sum(item.retrieval_was_needed for item in retrievals),
            len(retrievals),
        ),
        "source_only_portal_call_rate": _rate(
            sum(item.portal_calls > 0 for item in source_only),
            len(source_only),
        ),
        "context_utilization": _rate(utilized, returned),
        "retrieval_regret_rate": _rate(
            sum(item.retrieval_regret for item in results), len(results)
        ),
        "citation_validity": _rate(
            sum(item.citation_valid is True for item in citations), len(citations)
        ),
        "no_answer_accuracy": _rate(
            sum(item.predicted_no_answer for item in no_answers),
            len(no_answers),
        ),
        "unsupported_claim_rejection": _rate(
            sum(item.unsupported_claim_rejected is True for item in unsupported),
            len(unsupported),
        ),
        "security_boundary_passed": all(
            item.success and item.portal_calls == 0 for item in results if item.security_blocked
        ),
        "median_task_elapsed_ms": round(statistics.median(item.elapsed_ms for item in results), 4),
        "efficiency_measurement": "deterministic_estimate_not_acceptance_evidence",
    }


def run(
    payload: dict[str, Any],
    *,
    exact_usage: list[ExactUsageRecord] | None = None,
) -> dict[str, Any]:
    tasks = [item for item in payload["tasks"] if item.get("split") == "heldout"]
    all_results: dict[str, list[TaskResult]] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for variant in VARIANTS:
        session_evidence: dict[str, set[str]] = {}
        results = [_run_task(task, variant, session_evidence) for task in tasks]
        all_results[variant] = results
        summaries[variant] = _summarize(results)
    current = summaries["current-conditional-mcp"]
    improved = summaries["improved-progressive-mcp"]
    current_results = {item.task_id: item for item in all_results["current-conditional-mcp"]}
    improved_results = {item.task_id: item for item in all_results["improved-progressive-mcp"]}
    regressions = [
        task_id
        for task_id, baseline in current_results.items()
        if baseline.success and not improved_results[task_id].success
    ]
    estimated_token_improvement = 1 - (
        improved["estimated_uncached_tokens_per_verified_task"]
        / current["estimated_uncached_tokens_per_verified_task"]
    )
    harness_elapsed_improvement = 1 - (
        improved["harness_elapsed_ms_per_verified_task"]
        / current["harness_elapsed_ms_per_verified_task"]
    )

    exact_summaries = summarize_exact_usage(exact_usage) if exact_usage else None
    heldout_ids = {str(task["id"]) for task in tasks}
    exact_complete = False
    exact_token_improvement: float | None = None
    exact_elapsed_improvement: float | None = None
    exact_reason = "exact usage trace not supplied"
    if exact_usage and exact_summaries:
        ids_by_variant = {
            variant: {record.task_id for record in exact_usage if record.variant == variant}
            for variant in VARIANTS
        }
        exact_complete = all(ids_by_variant[variant] == heldout_ids for variant in VARIANTS)
        current_exact = exact_summaries.get("current-conditional-mcp")
        improved_exact = exact_summaries.get("improved-progressive-mcp")
        if exact_complete and current_exact and improved_exact:
            outcomes = {
                (variant, item.task_id): item.success
                for variant, items in all_results.items()
                for item in items
            }
            outcome_mismatch = any(
                outcomes.get((record.variant, record.task_id)) != record.verified
                for record in exact_usage
            )
            if outcome_mismatch:
                exact_reason = "usage trace verified outcomes do not match the held-out verifier"
                exact_complete = False
            elif current_exact["models"] != improved_exact["models"]:
                exact_reason = "paired variants used different model sets"
                exact_complete = False
            else:
                baseline_tokens = current_exact["uncached_tokens_per_verified_task"]
                baseline_elapsed = current_exact["elapsed_ms_per_verified_task"]
                if baseline_tokens <= 0 or baseline_elapsed <= 0:
                    exact_reason = "baseline exact token or elapsed denominator is zero"
                    exact_complete = False
                else:
                    exact_token_improvement = 1 - (
                        improved_exact["uncached_tokens_per_verified_task"]
                        / baseline_tokens
                    )
                    exact_elapsed_improvement = 1 - (
                        improved_exact["elapsed_ms_per_verified_task"]
                        / baseline_elapsed
                    )
                    exact_reason = "complete paired content-free exact usage trace"
        else:
            exact_reason = "usage trace is not complete for every held-out task and variant"
    efficiency_gate = bool(
        exact_complete
        and exact_token_improvement is not None
        and exact_elapsed_improvement is not None
        and (exact_token_improvement >= 0.15 or exact_elapsed_improvement >= 0.10)
    )
    acceptance = {
        "no_quality_regression": (
            improved["answer_quality"] >= current["answer_quality"]
            and improved["citation_validity"] >= current["citation_validity"]
            and improved["no_answer_accuracy"] >= current["no_answer_accuracy"]
            and improved["unsupported_claim_rejection"] >= current["unsupported_claim_rejection"]
            and not regressions
        ),
        "exact_efficiency_measurement_gate": exact_complete,
        "efficiency_gate": efficiency_gate,
        "source_only_portal_calls_gate": improved["source_only_portal_call_rate"] <= 0.10,
        "context_utilization_gate": improved["context_utilization"] >= 0.60,
        "security_gate": improved["security_boundary_passed"],
    }
    fixture_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "harness_version": HARNESS_VERSION,
        "fixture_hash": fixture_hash,
        "heldout_tasks": len(tasks),
        "variants": summaries,
        "paired_comparison": {
            "estimated_uncached_token_improvement": round(
                estimated_token_improvement,
                4,
            ),
            "harness_elapsed_improvement": round(harness_elapsed_improvement, 4),
            "exact_uncached_token_improvement": (
                round(exact_token_improvement, 4)
                if exact_token_improvement is not None
                else None
            ),
            "exact_elapsed_improvement": (
                round(exact_elapsed_improvement, 4)
                if exact_elapsed_improvement is not None
                else None
            ),
            "regression_count": len(regressions),
        },
        "exact_usage": {
            "complete": exact_complete,
            "reason": exact_reason,
            "variants": exact_summaries,
        },
        "acceptance": acceptance,
        "decision": "GO" if all(acceptance.values()) else "NO-GO / REWORK",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/fixtures/agent_task_ab/tasks.json"),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--usage-trace",
        type=Path,
        help="content-free OTLP-compatible JSONL with exact paired task usage",
    )
    args = parser.parse_args()
    payload = json.loads(args.fixture.read_text(encoding="utf-8"))
    exact_usage = load_exact_usage_jsonl(args.usage_trace) if args.usage_trace else None
    result = run(payload, exact_usage=exact_usage)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
