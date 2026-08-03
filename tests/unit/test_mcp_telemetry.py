from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from lkp_indexer.mcp_progressive import ProgressiveRetrievalConfig
from lkp_indexer.mcp_telemetry import ContentFreeMcpTelemetry


def _load_summarizer():
    script = Path(__file__).parents[2] / "scripts" / "summarize_mcp_telemetry.py"
    spec = importlib.util.spec_from_file_location("mcp_telemetry_summary", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.load_events, module.summarize


def test_telemetry_is_content_free_and_links_verification(tmp_path) -> None:
    telemetry = ContentFreeMcpTelemetry(
        tmp_path,
        config=ProgressiveRetrievalConfig(
            enabled=True,
            source_first=True,
            compact_cards=True,
            session_dedup=True,
            query_focused_compression=True,
            mmr_enabled=True,
            conflict_gate=True,
        ),
    )
    retrieval_id = "00000000-0000-0000-0000-000000000001"
    telemetry.record(
        "retrieve_context",
        {
            "query": "sensitive query must not be logged",
            "project": "sensitive-project",
            "retrieval_id": retrieval_id,
            "purpose": "failure",
            "confidence": "high",
            "no_answer": False,
            "contexts": [{"content": "sensitive evidence"}],
            "retrieval_runtime": {"mode": "hybrid", "reason": None},
            "metrics": {
                "api_calls": 1,
                "context_count": 1,
                "context_tokens": 42,
                "retained_state_hits": 1,
                "estimated_retained_state_tokens_saved": 80,
                "subqueries": ["must not be logged"],
            },
        },
    )
    telemetry.record(
        "verify_answer",
        {
            "retrieval_id": retrieval_id,
            "status": "verified",
            "attempt": 1,
            "answer": "sensitive answer",
            "verification": {"failures": []},
            "repair_allowed": False,
            "no_answer": False,
        },
    )

    target = next(tmp_path.glob("mcp-usage-*.jsonl"))
    raw = target.read_text(encoding="utf-8")
    events = [json.loads(line) for line in raw.splitlines()]

    assert len(events) == 2
    assert events[0]["variant"] == "improved-progressive-mcp"
    assert events[0]["metrics"]["context_tokens"] == 42
    assert events[1]["outcome"] == "verified"
    assert events[1]["retrieval_event_id"] == events[0]["event_id"]
    for sensitive in (
        "sensitive query must not be logged",
        "sensitive-project",
        "sensitive evidence",
        "must not be logged",
        "sensitive answer",
        retrieval_id,
    ):
        assert sensitive not in raw

    load_events, summarize = _load_summarizer()
    summary = summarize(load_events(tmp_path))
    assert summary["retrieval"]["calls"] == 1
    assert summary["retrieval"]["retained_state_hits"] == 1
    assert summary["retrieval"]["estimated_retained_state_tokens_saved"] == 80
    assert summary["verification"]["verified_rate"] == 1.0


def test_disabled_telemetry_writes_nothing(tmp_path) -> None:
    telemetry = ContentFreeMcpTelemetry(
        None,
        config=ProgressiveRetrievalConfig(enabled=True),
    )

    telemetry.record("retrieve_context", {"no_answer": True})

    assert list(tmp_path.iterdir()) == []
