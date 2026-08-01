from __future__ import annotations

import json

import httpx
from lkp_indexer.codex_mcp import PortalClient, call_tool, handle_message


def _transport(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/v1/rag/context":
        payload = json.loads(request.content)
        assert payload == {
            "query": "MTP",
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
                        "provenance": {
                            "document_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                            "chunk_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
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
                "content_hash": "abc123",
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
        assert "exact failure" in initialized["result"]["instructions"]

        listed = handle_message(
            client,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert listed is not None
        assert [item["name"] for item in listed["result"]["tools"]] == [
            "retrieve_context",
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
    assert payload["confidence"] == "low"
    assert payload["no_answer"] is True
    assert payload["navigation_available"] is True
    assert payload["retrieval_runtime"] == {
        "mode": "keyword-fallback",
        "reason": "gpu_recovery_pending",
    }
    assert payload["metrics"]["context_count"] == 0
    assert payload["metrics"]["context_chars"] == 0
    assert payload["metrics"]["navigation_count"] == 1
    assert "검증된 번역 런타임 근거" in result["content"][0]["text"]


def test_get_source_returns_only_selected_current_chunk() -> None:
    client = PortalClient(transport=httpx.MockTransport(_transport))
    try:
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
        '\ufeff{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'.lstrip(
            "\ufeff"
        )
    )
    client = PortalClient(transport=httpx.MockTransport(_transport))
    try:
        response = handle_message(client, message)
    finally:
        client.close()

    assert response is not None
    assert response["result"]["serverInfo"]["name"] == "local-knowledge"


def test_multi_intent_query_falls_back_to_distinctive_terms_and_caps_context() -> None:
    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/embedding/recovery":
            return httpx.Response(200, json={"runtime": {"open": False}})
        payload = json.loads(request.content)
        query = payload["query"]
        if query not in {"MTP", "SGLang"}:
            return httpx.Response(
                200,
                json={"confidence": "none", "no_answer": True, "context": []},
            )
        marker = "a" if query == "MTP" else "b"
        return httpx.Response(
            200,
            json={
                "confidence": "high",
                "no_answer": False,
                "context": [
                    {
                        "content": marker * 900,
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
        "MTP",
        "SGLang",
    ]
    assert len(payload["contexts"]) == 2
    assert payload["contexts"][1]["truncated"] is True
