#!/usr/bin/env python3
"""Summarize content-free MCP telemetry without opening evidence or prompts."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _average(values: list[float]) -> float | None:
    return round(statistics.mean(values), 4) if values else None


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return round(ordered[index], 4)


def load_events(directory: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in sorted(directory.glob("mcp-usage-*.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("schema_version") == "lkp-mcp-telemetry-v1":
                events.append(event)
    return events


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    retrievals = [event for event in events if event.get("tool") == "retrieve_context"]
    verifications = [event for event in events if event.get("tool") == "verify_answer"]
    retrieval_outcomes = Counter(str(event.get("outcome")) for event in retrievals)
    verification_outcomes = Counter(str(event.get("outcome")) for event in verifications)
    elapsed = [
        float((event.get("metrics") or {}).get("elapsed_ms"))
        for event in retrievals
        if isinstance((event.get("metrics") or {}).get("elapsed_ms"), (int, float))
    ]
    context_tokens = [
        float((event.get("metrics") or {}).get("context_tokens"))
        for event in retrievals
        if isinstance((event.get("metrics") or {}).get("context_tokens"), (int, float))
    ]
    retained_hits = sum(
        int((event.get("metrics") or {}).get("retained_state_hits") or 0)
        for event in retrievals
    )
    retained_savings = sum(
        int(
            (event.get("metrics") or {}).get(
                "estimated_retained_state_tokens_saved"
            )
            or 0
        )
        for event in retrievals
    )
    early_stops = sum(
        bool((event.get("metrics") or {}).get("early_stop")) for event in retrievals
    )
    source_first_blocks = sum(
        event.get("runtime_reason") == "source_first_gate" for event in retrievals
    )
    timestamps = sorted(
        str(event["recorded_at"])
        for event in events
        if isinstance(event.get("recorded_at"), str)
    )
    return {
        "schema_version": "lkp-mcp-telemetry-summary-v1",
        "period": {
            "first_recorded_at": timestamps[0] if timestamps else None,
            "last_recorded_at": timestamps[-1] if timestamps else None,
        },
        "events": len(events),
        "variants": dict(Counter(str(event.get("variant")) for event in events)),
        "retrieval": {
            "calls": len(retrievals),
            "outcomes": dict(retrieval_outcomes),
            "evidence_return_rate": _rate(
                retrieval_outcomes["evidence_returned"],
                len(retrievals),
            ),
            "source_first_blocks": source_first_blocks,
            "early_stop_rate": _rate(early_stops, len(retrievals)),
            "mean_context_tokens": _average(context_tokens),
            "mean_elapsed_ms": _average(elapsed),
            "p50_elapsed_ms": _percentile(elapsed, 0.50),
            "p95_elapsed_ms": _percentile(elapsed, 0.95),
            "retained_state_hits": retained_hits,
            "estimated_retained_state_tokens_saved": retained_savings,
        },
        "verification": {
            "calls": len(verifications),
            "outcomes": dict(verification_outcomes),
            "verified_rate": _rate(
                verification_outcomes["verified"],
                len(verifications),
            ),
        },
        "limitations": [
            "Live telemetry has no ground-truth task label.",
            "Exact model uncached tokens require a paired Codex usage trace.",
            "Estimated retained-state savings cover evidence text only.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(load_events(args.directory))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
