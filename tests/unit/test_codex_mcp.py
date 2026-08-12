from __future__ import annotations

import json

import httpx
from lkp_indexer.codex_mcp import (
    PortalClient,
    _repository_project,
    _subqueries,
    call_tool,
    handle_message,
)


def _transport(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/rag/context":
        payload = json.loads(request.content)
        assert payload == {
            "query": "MTP 번역 런타임 실패 원인",
            "top_k": 5,
            "max_chars": 6000,
            "filters": {"project": "need"},
        }
        return httpx.Response(
            200,
            json={
                "query": payload["query"],
                "confidence": "high",
                "no_answer": False,
                "context": [
                    {
                        "content": "검증된 번역 런타임 근거",
                        "retrieval_score": 0.82,
                        "evidence_level": "source",
                        "current": True,
                        "revision_match": True,
                        "project": "need",
                        "provenance": {
                            "document_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                            "chunk_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                            "relative_path": "README.md",
                            "content_hash": "a" * 64,
                            "start_line": 10,
                            "end_line": 20,
                        },
                    }
                ],
            },
        )
    if request.url.path == "/api/v1/embedding/recovery":
        return httpx.Response(
            200,
            json={"runtime": {"open": True, "reason": "gpu_recovery_pending"}},
        )
    if request.url.path == "/api/v1/documents/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa":
        assert request.url.params["chunk_page_size"] == "200"
        return httpx.Response(
            200,
            json={
                "id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "project": "need",
                "canonical_path": "/source/README.md",
                "relative_path": "README.md",
                "content_hash": "a" * 64,
                "current_version_id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                "chunk_total": 1,
                "chunks": [
                    {
                        "id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                        "start_line": 10,
                        "end_line": 20,
                        "content": "검증된 번역 런타임 근거",
                    }
                ],
            },
        )
    return httpx.Response(404)


def test_repository_project_refreshes_new_snapshot_without_mcp_restart() -> None:
    calls = 0

    def transport(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        assert request.url.path == "/api/v1/repository-analysis/projects"
        calls += 1
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "11111111-1111-4111-8111-111111111111",
                        "canonical_name": "portal",
                        "display_name": "Portal",
                        "snapshot_id": "22222222-2222-4222-8222-222222222222",
                        "stale": calls == 1,
                    }
                ]
            },
        )

    client = PortalClient(transport=httpx.MockTransport(transport))
    try:
        assert _repository_project(client, "portal", "portal architecture") is None
        current = _repository_project(client, "portal", "portal architecture")
    finally:
        client.close()

    assert current is not None
    assert current["snapshot_id"] == "22222222-2222-4222-8222-222222222222"
    assert calls == 2


def test_initialize_and_tool_discovery() -> None:
    client = PortalClient(transport=httpx.MockTransport(_transport))
    try:
        initialized = handle_message(
            client,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            },
        )
        assert initialized is not None
        assert initialized["result"]["serverInfo"]["name"] == "local-knowledge"
        assert "verify_answer" in initialized["result"]["instructions"]

        listed = handle_message(
            client,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert listed is not None
        assert [item["name"] for item in listed["result"]["tools"]] == [
            "retrieve_context",
            "verify_answer",
            "get_source",
        ]
        assert all(item["annotations"]["readOnlyHint"] for item in listed["result"]["tools"])
    finally:
        client.close()


def test_retrieve_context_is_bounded_and_exposes_runtime() -> None:
    client = PortalClient(transport=httpx.MockTransport(_transport))
    try:
        result = call_tool(
            client,
            "retrieve_context",
            {
                "query": "MTP 번역 런타임 실패 원인",
                "project": "need",
                "purpose": "failure",
            },
        )
    finally:
        client.close()

    assert result.get("isError") is None
    payload = result["structuredContent"]
    assert payload["confidence"] == "high"
    assert payload["no_answer"] is False
    assert payload["navigation_available"] is False
    assert payload["retrieval_runtime"] == {
        "mode": "keyword-fallback",
        "reason": "gpu_recovery_pending",
    }
    assert payload["metrics"]["context_count"] == 1
    assert payload["metrics"]["context_chars"] > 0
    assert payload["metrics"]["navigation_count"] == 0
    assert "검증된 번역 런타임 근거" in result["content"][0]["text"]


def test_get_source_returns_only_selected_current_chunk() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/embedding/recovery":
            return httpx.Response(200, json={"runtime": {"open": False}})
        return _transport(request)

    client = PortalClient(transport=httpx.MockTransport(transport))
    try:
        call_tool(
            client,
            "retrieve_context",
            {
                "query": "MTP 번역 런타임 실패 원인",
                "project": "need",
                "purpose": "failure",
            },
        )
        result = call_tool(
            client,
            "get_source",
            {
                "document_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "chunk_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            },
        )
    finally:
        client.close()

    assert result.get("isError") is None
    assert result["structuredContent"]["document"]["project"] == "need"
    assert result["structuredContent"]["chunk"]["content"] == "검증된 번역 런타임 근거"


def test_invalid_or_unknown_tool_fails_closed() -> None:
    client = PortalClient(transport=httpx.MockTransport(_transport))
    try:
        invalid = call_tool(client, "retrieve_context", {"query": "", "top_k": 99})
        unknown = call_tool(client, "write_document", {})
    finally:
        client.close()

    assert invalid["isError"] is True
    assert unknown["isError"] is True
    assert "unknown tool" in unknown["structuredContent"]["error"]


def test_initialize_accepts_a_utf8_bom_payload() -> None:
    message = json.loads(
        '\ufeff{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'.lstrip("\ufeff")
    )
    client = PortalClient(transport=httpx.MockTransport(_transport))
    try:
        response = handle_message(client, message)
    finally:
        client.close()

    assert response is not None
    assert response["result"]["serverInfo"]["name"] == "local-knowledge"


def test_multi_intent_query_preserves_each_clause_and_caps_context() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/embedding/recovery":
            return httpx.Response(200, json={"runtime": {"open": False}})
        payload = json.loads(request.content)
        query = payload["query"]
        if query not in {
            "12B MTP serving validation",
            "why SGLang failed for 31B",
        }:
            return httpx.Response(
                200,
                json={"confidence": "none", "no_answer": True, "context": []},
            )
        marker = "a" if query.startswith("12B") else "b"
        return httpx.Response(
            200,
            json={
                "confidence": "high",
                "no_answer": False,
                "context": [
                    {
                        "content": marker * 900,
                        "evidence_level": "source",
                        "provenance": {"chunk_id": marker * 8},
                    }
                ],
            },
        )

    client = PortalClient(transport=httpx.MockTransport(transport))
    try:
        result = call_tool(
            client,
            "retrieve_context",
            {
                "query": "12B MTP serving validation; why SGLang failed for 31B",
                "project": "local-voice-agent",
                "purpose": "decision",
                "max_chars": 1200,
            },
        )
    finally:
        client.close()

    payload = result["structuredContent"]
    assert payload["confidence"] == "high"
    assert payload["metrics"]["api_calls"] == 2
    assert payload["metrics"]["context_chars"] == 1200
    assert payload["metrics"]["subqueries"] == [
        "12B MTP serving validation",
        "why SGLang failed for 31B",
    ]
    assert len(payload["contexts"]) == 2
    assert payload["contexts"][1]["truncated"] is True


def test_long_query_is_split_inside_the_api_contract() -> None:
    query = "word " * 190

    parts = _subqueries(query)

    assert len(parts) == 2
    assert all(1 <= len(item) <= 500 for item in parts)
    assert "".join(parts).replace(" ", "") in query.replace(" ", "")


def test_prohibited_architecture_scope_fails_closed_before_any_api_call() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"prohibited request reached local API: {request.url.path}")

    client = PortalClient(transport=httpx.MockTransport(transport))
    try:
        retrieved = call_tool(
            client,
            "retrieve_context",
            {"query": "ESB가 요청을 처리하는 구조와 라이프사이클"},
        )["structuredContent"]
    finally:
        client.close()

    assert retrieved["requested_purpose"] == "general"
    assert retrieved["purpose"] == "architecture"
    assert retrieved["effective_project"] is None
    assert retrieved["confidence"] == "none"
    assert retrieved["no_answer"] is True
    assert retrieved["navigation_available"] is False
    assert retrieved["contexts"] == []
    assert retrieved["policy"] == "project_scope_denied"
    assert retrieved["retrieval_runtime"] == {
        "mode": "not_called",
        "reason": "project_scope_denied",
    }
    assert retrieved["metrics"]["api_calls"] == 0


def test_hard_gate_runs_before_top_k_selection() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/rag/context":
            return httpx.Response(
                200,
                json={
                    "confidence": "high",
                    "context": [
                        {
                            "content": "An operator reported a guess.",
                            "evidence_level": "reported",
                            "retrieval_score": 0.9,
                            "provenance": {"chunk_id": "reported"},
                        },
                        {
                            "content": "The database lease expired.",
                            "evidence_level": "source",
                            "retrieval_score": 0.02,
                            "provenance": {"chunk_id": "source"},
                        },
                    ],
                },
            )
        if request.url.path == "/api/v1/embedding/recovery":
            return httpx.Response(200, json={"runtime": {"open": False}})
        return httpx.Response(404)

    client = PortalClient(transport=httpx.MockTransport(transport))
    try:
        payload = call_tool(
            client,
            "retrieve_context",
            {"query": "database lease evidence", "top_k": 1},
        )["structuredContent"]
    finally:
        client.close()

    assert payload["no_answer"] is False
    assert [item["provenance"]["chunk_id"] for item in payload["contexts"]] == ["source"]
