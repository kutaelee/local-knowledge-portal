from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from lkp_indexer.agent_eval_telemetry import ExactUsageRecord


def _load_harness_run():
    script = Path(__file__).parents[2] / "scripts" / "run_agent_task_ab.py"
    spec = importlib.util.spec_from_file_location("agent_task_ab_harness", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.run


run = _load_harness_run()


def _payload() -> dict:
    return {
        "schema_version": "agent-task-ab-v2",
        "tasks": [
            {
                "id": "heldout-source",
                "split": "heldout",
                "task_type": "source_only",
                "project": "synthetic-safe",
                "query": "Where is build_index defined?",
                "expected_claim": "build_index is defined in src/indexer.py.",
                "source": [
                    {
                        "id": "source-1",
                        "content": "build_index is defined in src/indexer.py.",
                        "evidence_level": "source",
                    }
                ],
                "ranges": {},
            }
        ],
    }


def _usage(variant: str, *, input_tokens: int, elapsed_ms: float) -> ExactUsageRecord:
    return ExactUsageRecord(
        span_id=f"span-{variant}",
        task_id="heldout-source",
        variant=variant,
        model="synthetic-local-model",
        input_tokens=input_tokens,
        cached_input_tokens=0,
        uncached_input_tokens=input_tokens,
        output_tokens=20,
        total_tokens=input_tokens + 20,
        model_latency_ms=elapsed_ms * 0.8,
        task_elapsed_ms=elapsed_ms,
        verified=True,
        tool_calls=1,
    )


def test_estimates_cannot_satisfy_efficiency_acceptance() -> None:
    result = run(_payload())

    assert result["acceptance"]["no_quality_regression"] is True
    assert result["acceptance"]["exact_efficiency_measurement_gate"] is False
    assert result["acceptance"]["efficiency_gate"] is False
    assert result["decision"] == "NO-GO / REWORK"


def test_complete_exact_paired_usage_can_satisfy_efficiency_acceptance() -> None:
    usage = [
        _usage("source-only", input_tokens=100, elapsed_ms=100),
        _usage("current-conditional-mcp", input_tokens=100, elapsed_ms=100),
        _usage("improved-progressive-mcp", input_tokens=80, elapsed_ms=85),
    ]

    result = run(_payload(), exact_usage=usage)

    assert result["acceptance"]["exact_efficiency_measurement_gate"] is True
    assert result["acceptance"]["efficiency_gate"] is True
    assert result["paired_comparison"]["exact_uncached_token_improvement"] == 0.2
    assert result["decision"] == "GO"
