"""Content-free exact token and latency telemetry for agent-task evaluation."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class TelemetryValidationError(ValueError):
    """Raised when a trace would make an exact efficiency claim unsafe."""


TELEMETRY_SCHEMA_VERSION = "lkp-agent-eval-otel-v1"
_CONTENT_ATTRIBUTES = {
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.system_instructions",
    "input.value",
    "output.value",
    "prompt",
    "completion",
}
_IDENTIFIER_VALUE = re.compile(r"^[A-Za-z0-9_./:@+-]{1,200}$")


@dataclass(frozen=True, slots=True)
class ExactUsageRecord:
    span_id: str
    task_id: str
    variant: str
    model: str
    input_tokens: int
    cached_input_tokens: int
    uncached_input_tokens: int
    output_tokens: int
    total_tokens: int
    model_latency_ms: float
    task_elapsed_ms: float
    verified: bool
    tool_calls: int


def _otel_value(value: object) -> object:
    if not isinstance(value, dict):
        return value
    if "intValue" in value:
        raw = value["intValue"]
        if isinstance(raw, bool) or not isinstance(raw, (int, str)):
            raise TelemetryValidationError("OTLP intValue must be an integer")
        try:
            return int(raw)
        except ValueError as exc:
            raise TelemetryValidationError("OTLP intValue must be an integer") from exc
    if "doubleValue" in value:
        raw = value["doubleValue"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            raise TelemetryValidationError("OTLP doubleValue must be numeric")
        try:
            return float(raw)
        except ValueError as exc:
            raise TelemetryValidationError("OTLP doubleValue must be numeric") from exc
    if "stringValue" in value:
        return value["stringValue"]
    if "boolValue" in value:
        raw = value["boolValue"]
        if not isinstance(raw, bool):
            raise TelemetryValidationError("OTLP boolValue must be a boolean")
        return raw
    if "value" in value:
        return _otel_value(value["value"])
    return value


def _attributes(record: dict[str, Any]) -> dict[str, object]:
    raw = record.get("attributes") or {}
    if isinstance(raw, dict):
        return {str(key): _otel_value(value) for key, value in raw.items()}
    if isinstance(raw, list):
        attributes: dict[str, object] = {}
        for item in raw:
            if isinstance(item, dict) and isinstance(item.get("key"), str):
                attributes[item["key"]] = _otel_value(item.get("value"))
        return attributes
    raise TelemetryValidationError("attributes must be an object or OTLP key/value list")


def _first(source: dict[str, object], *keys: str) -> object | None:
    return next((source[key] for key in keys if key in source), None)


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TelemetryValidationError(f"{name} must be an integer")
    if value < 0:
        raise TelemetryValidationError(f"{name} must be non-negative")
    return value


def _boolean(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    raise TelemetryValidationError(f"{name} must be a boolean")


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryValidationError(f"{name} must be numeric")
    parsed = float(value)
    if parsed < 0:
        raise TelemetryValidationError(f"{name} must be non-negative")
    return parsed


def _required_string(value: object, name: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TelemetryValidationError(f"{name} must be a non-empty string")
    parsed = value.strip()
    if len(parsed) > maximum:
        raise TelemetryValidationError(f"{name} exceeds the allowed length")
    return parsed


def _required_identifier(value: object, name: str) -> str:
    parsed = _required_string(value, name)
    if not _IDENTIFIER_VALUE.fullmatch(parsed):
        raise TelemetryValidationError(f"{name} must be a bounded identifier")
    return parsed


def _is_content_attribute(key: str) -> bool:
    normalized = key.casefold()
    if normalized in _CONTENT_ATTRIBUTES:
        return True
    return any(
        marker in normalized
        for marker in (
            ".completion",
            ".message",
            ".prompt",
            ".system_instruction",
        )
    ) or normalized.endswith((".body", ".content", ".text"))


def _schema_version(record: dict[str, Any], attributes: dict[str, object]) -> str:
    raw = (
        _first(attributes, "lkp.eval.telemetry.schema", "telemetry.schema")
        or record.get("schemaUrl")
        or record.get("schema_url")
        or ""
    )
    schema = _required_string(raw, "telemetry schema")
    if schema == TELEMETRY_SCHEMA_VERSION or schema.startswith(
        "https://opentelemetry.io/schemas/"
    ):
        return schema
    raise TelemetryValidationError("supported telemetry schema provenance is required")


def _latency_ms(record: dict[str, Any], attributes: dict[str, object]) -> float:
    explicit = _first(
        attributes,
        "lkp.eval.model_latency_ms",
        "model_latency_ms",
    )
    if explicit is not None:
        latency = _number(explicit, "model latency")
    elif "gen_ai.client.operation.duration" in attributes:
        latency = _number(
            attributes["gen_ai.client.operation.duration"],
            "operation duration",
        ) * 1000
    else:
        start = record.get("startTimeUnixNano") or record.get("start_time_unix_nano")
        end = record.get("endTimeUnixNano") or record.get("end_time_unix_nano")
        if start is None or end is None:
            raise TelemetryValidationError("exact model latency is missing")
        latency = (int(end) - int(start)) / 1_000_000
    return latency


def _task_elapsed_ms(attributes: dict[str, object]) -> float:
    explicit = _first(
        attributes,
        "lkp.eval.task_elapsed_ms",
        "evaluation.task_elapsed_ms",
        "task_elapsed_ms",
    )
    if explicit is None:
        raise TelemetryValidationError("exact end-to-end task elapsed time is missing")
    return _number(explicit, "task elapsed time")


def parse_exact_usage_span(record: dict[str, Any]) -> ExactUsageRecord:
    """Parse an OTel-compatible span without accepting prompt or response text."""

    forbidden_top_level = {
        "body",
        "completion",
        "events",
        "input",
        "output",
        "prompt",
    }
    if any(key.casefold() in forbidden_top_level for key in record):
        raise TelemetryValidationError("content-bearing telemetry fields are forbidden")
    attributes = _attributes(record)
    _schema_version(record, attributes)
    leaked = sorted(key for key in attributes if _is_content_attribute(key))
    if leaked:
        raise TelemetryValidationError("content-bearing telemetry attributes are forbidden")

    span_id = _required_identifier(
        record.get("spanId")
        or record.get("span_id")
        or _first(attributes, "lkp.eval.generation.span_id")
        or "",
        "span_id",
    )
    task_id = _required_identifier(
        _first(attributes, "lkp.eval.task.id", "evaluation.task.id", "task_id"),
        "task_id",
    )
    variant = _required_identifier(
        _first(attributes, "lkp.eval.variant", "evaluation.variant", "variant"),
        "variant",
    )
    model = _required_identifier(
        _first(
            attributes,
            "gen_ai.response.model",
            "gen_ai.request.model",
            "model",
        ),
        "model",
    )

    input_tokens = _integer(
        _first(attributes, "gen_ai.usage.input_tokens", "input_tokens"),
        "input_tokens",
    )
    output_tokens = _integer(
        _first(attributes, "gen_ai.usage.output_tokens", "output_tokens"),
        "output_tokens",
    )
    cached_raw = _first(
        attributes,
        "gen_ai.usage.cache_read.input_tokens",
        "cached_input_tokens",
    )
    if cached_raw is None:
        cache_status = str(_first(attributes, "lkp.eval.cache.status", "cache_status") or "")
        if cache_status != "disabled":
            raise TelemetryValidationError(
                "cached input tokens are missing and cache is not explicitly disabled"
            )
        cached_input_tokens = 0
    else:
        cached_input_tokens = _integer(cached_raw, "cached_input_tokens")
    if cached_input_tokens > input_tokens:
        raise TelemetryValidationError("cached input tokens cannot exceed input tokens")

    verified = _boolean(
        _first(attributes, "lkp.eval.verified", "evaluation.verified", "verified"),
        "verified",
    )
    tool_calls_raw = _first(attributes, "lkp.eval.tool_calls", "tool_calls")
    tool_calls = _integer(tool_calls_raw if tool_calls_raw is not None else 0, "tool_calls")
    return ExactUsageRecord(
        span_id=span_id,
        task_id=task_id,
        variant=variant,
        model=model,
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        uncached_input_tokens=input_tokens - cached_input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        model_latency_ms=round(_latency_ms(record, attributes), 6),
        task_elapsed_ms=round(_task_elapsed_ms(attributes), 6),
        verified=verified,
        tool_calls=tool_calls,
    )


def load_exact_usage_jsonl(path: Path) -> list[ExactUsageRecord]:
    records: list[ExactUsageRecord] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            records.append(parse_exact_usage_span(payload))
        except (json.JSONDecodeError, TelemetryValidationError) as exc:
            raise TelemetryValidationError(f"invalid telemetry line {line_number}: {exc}") from exc
    if not records:
        raise TelemetryValidationError("telemetry file contains no records")
    return records


def summarize_exact_usage(records: Iterable[ExactUsageRecord]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[ExactUsageRecord]] = {}
    seen_tasks: set[tuple[str, str]] = set()
    seen_spans: dict[str, ExactUsageRecord] = {}
    for record in records:
        if record.span_id in seen_spans:
            if seen_spans[record.span_id] != record:
                raise TelemetryValidationError("duplicate span id has conflicting metrics")
            continue
        seen_spans[record.span_id] = record
        identity = (record.variant, record.task_id)
        if identity in seen_tasks:
            raise TelemetryValidationError("duplicate task/variant telemetry record")
        seen_tasks.add(identity)
        grouped.setdefault(record.variant, []).append(record)

    summaries: dict[str, dict[str, Any]] = {}
    for variant, items in grouped.items():
        verified = sum(item.verified for item in items)
        if verified == 0:
            raise TelemetryValidationError(f"variant {variant} has no verified tasks")
        uncached = sum(item.uncached_input_tokens for item in items)
        total = sum(item.total_tokens for item in items)
        summaries[variant] = {
            "records": len(items),
            "verified_tasks": verified,
            "verified_tasks_per_million_uncached_tokens": round(
                verified * 1_000_000 / max(1, uncached),
                6,
            ),
            "uncached_tokens_per_verified_task": round(uncached / verified, 6),
            "total_tokens_per_verified_task": round(total / verified, 6),
            "model_latency_ms_per_verified_task": round(
                sum(item.model_latency_ms for item in items) / verified,
                6,
            ),
            "elapsed_ms_per_verified_task": round(
                sum(item.task_elapsed_ms for item in items) / verified,
                6,
            ),
            "tool_calls_per_verified_task": round(
                sum(item.tool_calls for item in items) / verified,
                6,
            ),
            "exact": True,
            "content_captured": False,
            "models": sorted({item.model for item in items}),
            "records_detail": [asdict(item) for item in items],
        }
    return summaries
