"""Content-free append-only telemetry for the local Codex MCP bridge."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .mcp_progressive import ProgressiveRetrievalConfig

SCHEMA_VERSION = "lkp-mcp-telemetry-v1"
_PURPOSES = {"general", "failure", "architecture", "repository", "decision", "experiment"}
_STATUSES = {"verified", "repair_required", "no_answer"}
_METRIC_KEYS = {
    "api_calls",
    "context_count",
    "context_chars",
    "context_tokens",
    "full_context_chars",
    "navigation_count",
    "navigation_chars",
    "early_stop",
    "visible_exact_conflict",
    "conflict_candidates_omitted",
    "candidate_count",
    "selected_tokens",
    "deduplicated_count",
    "redundant_suppressed_count",
    "exact_anchor_retained",
    "best_selection_score",
    "elapsed_ms",
    "retained_state_hits",
    "estimated_retained_state_tokens_saved",
}


class ContentFreeMcpTelemetry:
    """Persist only counters and verifier outcomes, never prompts or evidence."""

    def __init__(
        self,
        directory: str | Path | None,
        *,
        config: ProgressiveRetrievalConfig,
    ) -> None:
        self.directory = Path(directory).expanduser() if directory else None
        self.process_id = str(uuid4())
        self.sequence = 0
        self.retrieval_refs: dict[str, str] = {}
        self.variant = (
            "improved-progressive-mcp"
            if config.enabled
            else "current-conditional-mcp"
        )
        self.features = {
            "source_first": config.source_first,
            "compact_cards": config.compact_cards,
            "session_dedup": config.session_dedup,
            "low_confidence_navigation": config.low_confidence_navigation,
            "query_focused_compression": config.query_focused_compression,
            "mmr": config.mmr_enabled,
            "conflict_gate": config.conflict_gate,
        }

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        config: ProgressiveRetrievalConfig,
    ) -> ContentFreeMcpTelemetry:
        directory = settings.mcp_telemetry_dir if settings.mcp_telemetry_enabled else None
        return cls(directory, config=config)

    def record(
        self,
        tool: str,
        payload: dict[str, Any],
        *,
        error_type: str | None = None,
    ) -> None:
        if self.directory is None:
            return
        self.sequence += 1
        event_id = f"{self.process_id}:{self.sequence}"
        event: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "recorded_at": datetime.now(UTC).isoformat(),
            "process_id": self.process_id,
            "event_id": event_id,
            "variant": self.variant,
            "tool": (
                tool
                if tool in {"retrieve_context", "verify_answer", "get_source"}
                else "unknown"
            ),
            "features": self.features,
        }
        if error_type:
            event.update({"outcome": "error", "error_type": error_type})
        elif tool == "retrieve_context":
            self._record_retrieval(event, event_id, payload)
        elif tool == "verify_answer":
            self._record_verification(event, payload)
        else:
            event["outcome"] = "ok"
        self._append(event)

    def _record_retrieval(
        self,
        event: dict[str, Any],
        event_id: str,
        payload: dict[str, Any],
    ) -> None:
        retrieval_id = payload.get("retrieval_id")
        if isinstance(retrieval_id, str) and retrieval_id:
            self.retrieval_refs[retrieval_id] = event_id
        purpose = str(payload.get("purpose") or "general")
        runtime = payload.get("retrieval_runtime") or {}
        raw_metrics = payload.get("metrics") or {}
        metrics = {
            key: value
            for key, value in raw_metrics.items()
            if key in _METRIC_KEYS and isinstance(value, (bool, int, float))
        }
        event.update(
            {
                "outcome": "no_answer" if payload.get("no_answer") else "evidence_returned",
                "purpose": purpose if purpose in _PURPOSES else "other",
                "confidence": str(payload.get("confidence") or "none"),
                "navigation_available": bool(payload.get("navigation_available")),
                "runtime_mode": str(runtime.get("mode") or "unknown"),
                "runtime_reason": str(runtime.get("reason") or "none"),
                "metrics": metrics,
            }
        )

    def _record_verification(
        self,
        event: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        retrieval_id = payload.get("retrieval_id")
        parent = self.retrieval_refs.get(str(retrieval_id)) if retrieval_id else None
        status = str(payload.get("status") or "no_answer")
        verification = payload.get("verification") or {}
        failures = verification.get("failures") or []
        event.update(
            {
                "outcome": status if status in _STATUSES else "no_answer",
                "retrieval_event_id": parent,
                "attempt": int(payload.get("attempt") or 0),
                "repair_allowed": bool(payload.get("repair_allowed")),
                "no_answer": bool(payload.get("no_answer")),
                "failure_count": len(failures) if isinstance(failures, list) else 0,
            }
        )

    def _append(self, event: dict[str, Any]) -> None:
        rendered = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        encoded = rendered.encode("utf-8")
        if len(encoded) > 8_192:
            return
        try:
            assert self.directory is not None
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = self.directory / f"mcp-usage-{datetime.now(UTC):%Y-%m-%d}.jsonl"
            descriptor = os.open(
                target,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(descriptor, encoded)
            finally:
                os.close(descriptor)
        except OSError:
            # Retrieval must stay available if optional telemetry storage is unavailable.
            return
