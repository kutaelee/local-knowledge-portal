from __future__ import annotations

from copy import deepcopy

import pytest
from lkp_indexer.agent_eval_telemetry import (
    TELEMETRY_SCHEMA_VERSION,
    TelemetryValidationError,
    parse_exact_usage_span,
    summarize_exact_usage,
)


def _span(*, span_id: str = "abc123", task_id: str = "task-a", variant: str = "current"):
    return {
        "spanId": span_id,
        "attributes": {
            "lkp.eval.telemetry.schema": TELEMETRY_SCHEMA_VERSION,
            "lkp.eval.task.id": task_id,
            "lkp.eval.variant": variant,
            "gen_ai.response.model": "synthetic-local-model",
            "gen_ai.usage.input_tokens": 120,
            "gen_ai.usage.cache_read.input_tokens": 20,
            "gen_ai.usage.output_tokens": 30,
            "lkp.eval.model_latency_ms": 250.5,
            "lkp.eval.task_elapsed_ms": 320.0,
            "lkp.eval.verified": True,
            "lkp.eval.tool_calls": 2,
        },
    }


def test_exact_usage_accepts_typed_otlp_integer_strings() -> None:
    record = _span()
    record["attributes"] = [
        {"key": key, "value": {"intValue": str(value)}}
        if isinstance(value, int) and not isinstance(value, bool)
        else {"key": key, "value": {"boolValue": value}}
        if isinstance(value, bool)
        else {"key": key, "value": {"doubleValue": value}}
        if isinstance(value, float)
        else {"key": key, "value": {"stringValue": value}}
        for key, value in record["attributes"].items()
    ]

    parsed = parse_exact_usage_span(record)

    assert parsed.uncached_input_tokens == 100
    assert parsed.total_tokens == 150
    assert parsed.model_latency_ms == 250.5
    assert parsed.task_elapsed_ms == 320.0


@pytest.mark.parametrize("invalid", ["120", True, -1])
def test_exact_usage_rejects_untyped_or_invalid_token_counts(invalid) -> None:
    record = _span()
    record["attributes"]["gen_ai.usage.input_tokens"] = invalid

    with pytest.raises(TelemetryValidationError, match="input_tokens"):
        parse_exact_usage_span(record)


def test_exact_usage_rejects_content_and_requires_cache_provenance() -> None:
    content_record = _span()
    content_record["attributes"]["gen_ai.input.messages"] = "forbidden-content"
    with pytest.raises(TelemetryValidationError, match="content-bearing"):
        parse_exact_usage_span(content_record)

    nested_content_record = _span()
    nested_content_record["attributes"]["gen_ai.input.messages.0.content"] = (
        "forbidden-content"
    )
    with pytest.raises(TelemetryValidationError, match="content-bearing"):
        parse_exact_usage_span(nested_content_record)

    custom_content_record = _span()
    custom_content_record["attributes"]["custom.prompt.text"] = "forbidden-content"
    with pytest.raises(TelemetryValidationError, match="content-bearing"):
        parse_exact_usage_span(custom_content_record)

    missing_cache = _span()
    del missing_cache["attributes"]["gen_ai.usage.cache_read.input_tokens"]
    with pytest.raises(TelemetryValidationError, match="cache"):
        parse_exact_usage_span(missing_cache)

    missing_cache["attributes"]["lkp.eval.cache.status"] = "disabled"
    assert parse_exact_usage_span(missing_cache).cached_input_tokens == 0


def test_exact_usage_requires_schema_and_does_not_coerce_latency_strings() -> None:
    missing_schema = _span()
    del missing_schema["attributes"]["lkp.eval.telemetry.schema"]
    with pytest.raises(TelemetryValidationError, match="schema"):
        parse_exact_usage_span(missing_schema)

    string_latency = _span()
    string_latency["attributes"]["lkp.eval.model_latency_ms"] = "250.5"
    with pytest.raises(TelemetryValidationError, match="latency"):
        parse_exact_usage_span(string_latency)


def test_summary_deduplicates_one_span_and_rejects_multiple_generations_per_task() -> None:
    first = parse_exact_usage_span(_span())
    summary = summarize_exact_usage([first, first])
    assert summary["current"]["records"] == 1
    assert summary["current"]["uncached_tokens_per_verified_task"] == 100

    second_generation = parse_exact_usage_span(_span(span_id="def456"))
    with pytest.raises(TelemetryValidationError, match="duplicate task/variant"):
        summarize_exact_usage([first, second_generation])


def test_summary_uses_only_exact_counts_for_paired_variants() -> None:
    current = parse_exact_usage_span(_span())
    improved_payload = deepcopy(_span(span_id="xyz789", variant="improved"))
    improved_payload["attributes"]["gen_ai.usage.input_tokens"] = 100
    improved_payload["attributes"]["gen_ai.usage.cache_read.input_tokens"] = 25
    improved = parse_exact_usage_span(improved_payload)

    summary = summarize_exact_usage([current, improved])

    assert summary["current"]["verified_tasks_per_million_uncached_tokens"] == 10_000
    assert summary["improved"]["uncached_tokens_per_verified_task"] == 75
