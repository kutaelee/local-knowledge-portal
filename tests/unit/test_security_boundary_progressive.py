from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from lkp.agent_evidence import estimate_tokens, query_focused_extract
from lkp.models import Document
from lkp.security_boundary import (
    ProhibitedProjectError,
    install_session_security_guards,
    is_prohibited_project,
    path_references_prohibited_project,
    require_allowed_project,
)
from lkp.settings import Settings
from lkp_indexer.codex_mcp import PortalClient, call_tool
from lkp_indexer.hook_collector import _spool_project_allowed
from lkp_indexer.hook_spool import spool
from lkp_indexer.mcp_progressive import ProgressiveRetrievalConfig, token_aware_select
from lkp_indexer.scanner import scan_root
from pydantic import ValidationError
from sqlalchemy.orm import Session


def _progressive_config(**overrides) -> ProgressiveRetrievalConfig:
    values = {
        "enabled": True,
        "source_first": True,
        "compact_cards": True,
        "session_dedup": True,
        "low_confidence_navigation": False,
        "evidence_token_budget": 600,
        "early_stop_score": 0.7,
    }
    values.update(overrides)
    return ProgressiveRetrievalConfig(**values)


def test_prohibited_project_is_normalized_and_graph_cannot_be_enabled() -> None:
    assert is_prohibited_project("  ESB  ")
    with pytest.raises(ProhibitedProjectError):
        require_allowed_project("ｅｓｂ")
    with pytest.raises(ValidationError):
        Settings(repository_graph_shadow_enabled=True)
    with pytest.raises(ValidationError):
        Settings(mcp_progressive_enabled=True)
    configured = Settings(
        mcp_progressive_enabled=True,
        mcp_agent_evidence_api_enabled=True,
    )
    assert configured.mcp_progressive_enabled is True
    with pytest.raises(ValidationError):
        Settings(mcp_mmr_enabled=True)
    with pytest.raises(ValidationError):
        Settings(
            mcp_progressive_enabled=True,
            mcp_agent_evidence_api_enabled=True,
            mcp_query_focused_compression_enabled=True,
        )


def test_scanner_does_not_open_or_queue_synthetic_prohibited_tree(tmp_path) -> None:
    source_root = tmp_path / "source"
    prohibited = source_root / "esb"
    prohibited.mkdir(parents=True)
    sentinel = prohibited / "must-not-be-read.md"
    sentinel.write_text("SYNTHETIC_PROHIBITED_SENTINEL", encoding="utf-8")
    root = SimpleNamespace(
        canonical_path=str(prohibited),
        source_type="repository_collection",
        exclude_patterns=[],
        include_patterns=["**/*"],
    )

    stats = scan_root(None, root, 1024)

    assert path_references_prohibited_project(prohibited)
    assert stats.visited == 0
    assert stats.queued == 0
    assert stats.ignored == 1


def test_session_persistence_guard_rejects_synthetic_prohibited_record() -> None:
    install_session_security_guards()
    with Session() as session:
        session.add(Document(project_key="esb"))
        with pytest.raises(ProhibitedProjectError):
            session.flush()


def test_hook_spool_drops_synthetic_prohibited_payload_without_writing(tmp_path) -> None:
    primary = tmp_path / "primary"
    fallback = tmp_path / "fallback"
    raw = json.dumps(
        {
            "hook_event_name": "Stop",
            "session_id": "synthetic-session",
            "cwd": r"C:\synthetic\esb",
            "result": "SYNTHETIC_PROHIBITED_SENTINEL",
        }
    ).encode()

    returned = spool(raw, primary, fallback)

    assert not returned.exists()
    assert not primary.exists()
    assert not fallback.exists()


def test_collector_prefix_gate_leaves_existing_synthetic_spool_untouched(tmp_path) -> None:
    spool_path = tmp_path / "synthetic.json"
    payload = json.dumps(
        {
            "cwd": r"C:\synthetic\esb",
            "payload": {"result": "SYNTHETIC_PROHIBITED_SENTINEL"},
        },
        sort_keys=True,
    )
    spool_path.write_text(payload, encoding="utf-8")

    assert _spool_project_allowed(spool_path) is False
    assert spool_path.read_text(encoding="utf-8") == payload


def test_mcp_policy_denial_makes_zero_network_calls_and_echoes_no_payload() -> None:
    calls = 0

    def transport(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("policy denial must precede network access")

    client = PortalClient(
        transport=httpx.MockTransport(transport),
        progressive_config=_progressive_config(),
    )
    try:
        result = call_tool(
            client,
            "retrieve_context",
            {
                "query": "SYNTHETIC_PROHIBITED_SENTINEL",
                "project": "esb",
                "trigger": "explicit_request",
            },
        )
    finally:
        client.close()

    rendered = json.dumps(result, ensure_ascii=False)
    assert calls == 0
    assert result["structuredContent"]["no_answer"] is True
    assert result["structuredContent"]["metrics"]["api_calls"] == 0
    assert "SYNTHETIC_PROHIBITED_SENTINEL" not in rendered
    assert '"esb"' not in rendered.casefold()


def test_source_first_gate_makes_zero_portal_calls() -> None:
    calls = 0

    def transport(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500)

    client = PortalClient(
        transport=httpx.MockTransport(transport),
        progressive_config=_progressive_config(),
    )
    try:
        result = call_tool(
            client,
            "retrieve_context",
            {"query": "exact implementation symbol", "purpose": "general"},
        )["structuredContent"]
    finally:
        client.close()

    assert calls == 0
    assert result["no_answer"] is True
    assert result["metrics"]["api_calls"] == 0
    assert result["retrieval_runtime"]["reason"] == "source_first_gate"


def test_get_source_rejects_ids_not_returned_by_allowed_retrieval() -> None:
    calls = 0

    def transport(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("unbound source IDs must be rejected before network access")

    client = PortalClient(transport=httpx.MockTransport(transport))
    try:
        result = call_tool(
            client,
            "get_source",
            {
                "document_id": "11111111-1111-4111-8111-111111111111",
                "chunk_id": "22222222-2222-4222-8222-222222222222",
            },
        )
    finally:
        client.close()

    assert calls == 0
    assert result["isError"] is True
    assert "not part of an allowed retrieval" in result["structuredContent"]["message"]


def test_progressive_retrieval_early_stops_compacts_and_deduplicates() -> None:
    calls: list[str] = []
    full_evidence = (
        "Lease recovery decision uses a bounded retry budget and verifies the current source hash. "
        * 12
    )

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v1/agent/evidence":
            return httpx.Response(
                200,
                json={
                    "confidence": "high",
                    "evidence": [
                        {
                            "schema_version": "agent-evidence-card-v1",
                            "evidence_id": "22222222-2222-4222-8222-222222222222",
                            "project": "safe-service",
                            "claim": "Lease recovery decision",
                            "discriminating_evidence": full_evidence,
                            "relevance": {"score": 0.91, "matched_terms": ["lease"]},
                            "source": {
                                "document_id": "11111111-1111-4111-8111-111111111111",
                                "chunk_id": "22222222-2222-4222-8222-222222222222",
                                "path": "docs/lease.md",
                                "start_line": 4,
                                "end_line": 8,
                                "source_hash": "a" * 64,
                            },
                            "verification": {
                                "status": "grounding",
                                "evidence_level": "source",
                                "confidence": "high",
                                "current": True,
                                "revision_match": True,
                            },
                            "counter_evidence": [],
                            "applicability_limits": [],
                        },
                        {
                            "schema_version": "agent-evidence-card-v1",
                            "evidence_id": "33333333-3333-4333-8333-333333333333",
                            "project": "safe-service",
                            "claim": "Unrelated current note",
                            "discriminating_evidence": "Unrelated current note about formatting.",
                            "relevance": {"score": 0.2, "matched_terms": []},
                            "source": {
                                "chunk_id": "33333333-3333-4333-8333-333333333333",
                                "path": "docs/formatting.md",
                                "start_line": 1,
                                "end_line": 2,
                                "source_hash": "b" * 64,
                            },
                            "verification": {
                                "status": "grounding",
                                "evidence_level": "source",
                                "confidence": "high",
                                "current": True,
                                "revision_match": True,
                            },
                            "counter_evidence": [],
                            "applicability_limits": [],
                        },
                    ],
                },
            )
        if request.url.path == "/api/v1/embedding/recovery":
            return httpx.Response(200, json={"runtime": {"open": False}})
        return httpx.Response(404)

    client = PortalClient(
        transport=httpx.MockTransport(transport),
        progressive_config=_progressive_config(),
    )
    arguments = {
        "query": "Lease recovery decision",
        "project": "safe-service",
        "purpose": "decision",
        "trigger": "prior_decision",
        "session_id": "session-a",
    }
    try:
        first = call_tool(client, "retrieve_context", arguments)["structuredContent"]
        second = call_tool(client, "retrieve_context", arguments)["structuredContent"]
    finally:
        client.close()

    assert first["no_answer"] is False
    assert first["metrics"]["early_stop"] is True
    assert first["metrics"]["context_count"] == 1
    assert first["contexts"][0]["compact"] is True
    assert "content" not in first["contexts"][0]
    assert first["contexts"][0]["verification"]["status"] == "grounding"
    assert first["contexts"][0]["next_action"]["tool"] == "get_source"
    assert estimate_tokens(first["contexts"][0]["discriminating_evidence"]) < estimate_tokens(
        full_evidence
    )
    assert second["no_answer"] is False
    assert second["metrics"]["deduplicated_count"] == 1
    assert second["contexts"][0]["deduplicated"] is True
    assert calls.count("/api/v1/agent/evidence") == 2


def test_progressive_hard_gates_stale_revision_scope_and_provenance() -> None:
    def card(
        evidence_id: str,
        *,
        project: str = "safe-service",
        current: bool = True,
        revision_match: bool = True,
        source_hash: str = "a" * 64,
    ) -> dict:
        return {
            "schema_version": "agent-evidence-card-v1",
            "evidence_id": evidence_id,
            "project": project,
            "claim": "A candidate that must not pass every hard gate.",
            "discriminating_evidence": "A candidate that must not pass every hard gate.",
            "relevance": {"score": 0.99, "matched_terms": ["candidate"]},
            "source": {
                "chunk_id": evidence_id,
                "path": "docs/candidate.md",
                "start_line": 1,
                "end_line": 2,
                "source_hash": source_hash,
            },
            "verification": {
                "status": "grounding",
                "evidence_level": "source",
                "confidence": "high",
                "current": current,
                "revision_match": revision_match,
            },
        }

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/agent/evidence":
            return httpx.Response(
                200,
                json={
                    "confidence": "high",
                    "evidence": [
                        card("stale", current=False),
                        card("revision", revision_match=False),
                        card("scope", project="other-service"),
                        card("provenance", source_hash="short"),
                    ],
                },
            )
        if request.url.path == "/api/v1/embedding/recovery":
            return httpx.Response(200, json={"runtime": {"open": False}})
        return httpx.Response(404)

    client = PortalClient(
        transport=httpx.MockTransport(transport),
        progressive_config=_progressive_config(),
    )
    try:
        result = call_tool(
            client,
            "retrieve_context",
            {
                "query": "candidate decision",
                "project": "safe-service",
                "purpose": "decision",
                "trigger": "prior_decision",
            },
        )["structuredContent"]
    finally:
        client.close()

    assert result["no_answer"] is True
    assert result["contexts"] == []
    assert result["navigation"] == []


def test_query_focused_extraction_preserves_late_exact_evidence_in_source_order() -> None:
    text = (
        "The precheck enables `MODE_SAFE`. "
        "This sentence only describes generic background. "
        "The retry outcome is written to docs/retry-result.md. "
        "The closing sentence is unrelated."
    )

    snippet, metrics = query_focused_extract(
        text,
        query="Where does MODE_SAFE write the retry outcome in docs/retry-result.md?",
        max_tokens=45,
    )

    assert "MODE_SAFE" in snippet
    assert "docs/retry-result.md" in snippet
    assert snippet.index("MODE_SAFE") < snippet.index("docs/retry-result.md")
    assert metrics["selected_unit_indices"] == sorted(metrics["selected_unit_indices"])
    assert metrics["exact_anchor_coverage"] == 1.0
    assert metrics["compressed_tokens"] < metrics["original_tokens"]


def test_query_focused_extraction_never_splits_late_anchor_at_tiny_budget() -> None:
    snippet, metrics = query_focused_extract(
        "alpha beta gamma delta epsilon zeta OMEGA_PATH",
        query="Where is OMEGA_PATH?",
        max_tokens=2,
    )

    assert snippet == "OMEGA_PATH"
    assert metrics["exact_anchor_coverage"] == 1.0
    assert metrics["budget_exceeded_for_atomic_anchor"] is True


def test_mmr_removes_true_duplicates_but_retains_conflicting_exact_values() -> None:
    contexts = [
        {
            "evidence_id": "limit-7-primary",
            "content": "The worker retry policy uses RETRY_LIMIT 7 after a timeout.",
            "evidence_level": "verified",
            "selection_score": 0.99,
        },
        {
            "evidence_id": "limit-7-duplicate",
            "content": "The worker retry policy uses RETRY_LIMIT 7 after a timeout.",
            "evidence_level": "verified",
            "selection_score": 0.90,
        },
        {
            "evidence_id": "limit-9-conflict",
            "content": "The worker retry policy uses RETRY_LIMIT 9 after a timeout.",
            "evidence_level": "verified",
            "selection_score": 0.94,
        },
    ]

    selected, metrics = token_aware_select(
        contexts,
        query="What retry policy does the worker use after a timeout?",
        top_k=3,
        token_budget=200,
        mmr_lambda=0.55,
    )

    assert {item["evidence_id"] for item in selected} == {
        "limit-7-primary",
        "limit-9-conflict",
    }
    assert metrics["redundant_suppressed_count"] == 1


def test_mcp_conflict_gate_cannot_be_bypassed_by_repair() -> None:
    first_id = "11111111-1111-4111-8111-111111111111"
    second_id = "22222222-2222-4222-8222-222222222222"
    client = PortalClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
        progressive_config=_progressive_config(conflict_gate=True),
    )
    retrieval_id = client.store_retrieval(
        [
            {
                "content": "The current policy sets RETRY_LIMIT to 7.",
                "evidence_level": "verified",
                "provenance": {"chunk_id": first_id},
            },
            {
                "content": "The current policy sets RETRY_LIMIT to 9.",
                "evidence_level": "verified",
                "provenance": {"chunk_id": second_id},
            },
        ]
    )
    candidates = [
        {
            "text": "The current policy sets RETRY_LIMIT to 7.",
            "claims": [
                {
                    "text": "The current policy sets RETRY_LIMIT to 7.",
                    "citations": [first_id],
                }
            ],
        },
        {
            "text": "The current policy sets RETRY_LIMIT to 9.",
            "claims": [
                {
                    "text": "The current policy sets RETRY_LIMIT to 9.",
                    "citations": [second_id],
                }
            ],
        },
    ]

    try:
        result = call_tool(
            client,
            "verify_answer",
            {"retrieval_id": retrieval_id, "candidates": candidates},
        )["structuredContent"]
    finally:
        client.close()

    assert result["status"] == "no_answer"
    assert result["repair_allowed"] is False
    assert result["no_answer"] is True
    assert "candidate_conflict" in result["verification"]["failures"]


def test_visible_exact_conflict_survives_progressive_early_stop() -> None:
    def card(evidence_id: str, value: int, score: float, content: str) -> dict:
        return {
            "schema_version": "agent-evidence-card-v1",
            "evidence_id": evidence_id,
            "project": "safe-service",
            "claim": content,
            "discriminating_evidence": content,
            "relevance": {"score": score, "matched_terms": ["retry_limit"]},
            "source": {
                "chunk_id": evidence_id,
                "path": f"docs/retry-{value}.md",
                "start_line": 1,
                "end_line": 2,
                "source_hash": str(value) * 64,
            },
            "verification": {
                "status": "grounding",
                "evidence_level": "verified",
                "confidence": "high",
                "current": True,
                "revision_match": True,
            },
        }

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/agent/evidence":
            return httpx.Response(
                200,
                json={
                    "confidence": "high",
                    "evidence": [
                        card(
                            "11111111-1111-4111-8111-111111111111",
                            7,
                            0.95,
                            "The current timeout policy sets RETRY_LIMIT to 7.",
                        ),
                        card(
                            "22222222-2222-4222-8222-222222222222",
                            9,
                            0.55,
                            "A competing record sets RETRY_LIMIT to 9.",
                        ),
                    ],
                },
            )
        if request.url.path == "/api/v1/embedding/recovery":
            return httpx.Response(200, json={"runtime": {"open": False}})
        return httpx.Response(404)

    client = PortalClient(
        transport=httpx.MockTransport(transport),
        progressive_config=_progressive_config(
            conflict_gate=True,
            mmr_enabled=True,
        ),
    )
    try:
        result = call_tool(
            client,
            "retrieve_context",
            {
                "query": "What RETRY_LIMIT applies to the current timeout policy?",
                "project": "safe-service",
                "purpose": "decision",
                "trigger": "prior_decision",
            },
        )["structuredContent"]
        limited = call_tool(
            client,
            "retrieve_context",
            {
                "query": "What RETRY_LIMIT applies to the current timeout policy?",
                "project": "safe-service",
                "purpose": "decision",
                "trigger": "prior_decision",
                "top_k": 1,
            },
        )["structuredContent"]
    finally:
        client.close()

    assert result["metrics"]["early_stop"] is True
    assert result["metrics"]["visible_exact_conflict"] is True
    assert result["metrics"]["context_count"] == 2
    assert limited["no_answer"] is True
    assert limited["contexts"] == []
    assert limited["metrics"]["conflict_candidates_omitted"] is True
