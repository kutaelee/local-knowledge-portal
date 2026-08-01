"""Small read-only MCP bridge from Codex to the Local Knowledge Portal."""

from __future__ import annotations

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID

import httpx

SERVER_NAME = "local-knowledge"
SERVER_VERSION = "0.1.0"
DEFAULT_BASE_URL = "http://127.0.0.1:8010"
SERVER_INSTRUCTIONS = (
    "Do not call retrieve_context before inspecting current source. Call it once when the user "
    "explicitly requests local knowledge, or when one bounded source search fails and an exact "
    "failure, regression, prior decision, or experiment may help. Do not call for architecture "
    "explanations or exact identifiers already answered by current source. Pass the project key. "
    "High-confidence cited context may support claims; low confidence is a navigation hint only. "
    "A no-answer result is not evidence. Use get_source only to verify a selected citation."
)

TOOLS = [
    {
        "name": "retrieve_context",
        "title": "Retrieve verified local knowledge context",
        "description": (
            "Conditionally retrieve a small, provenance-bearing context from the local knowledge "
            "portal. Call when the user requests local knowledge, or after one bounded source "
            "search misses and a failure, regression, prior decision, or experiment may help. "
            "Do not call when current source already answers the question. The backend "
            "performs scope routing, wide candidate generation, reward reranking, stale/revision "
            "gates, adjacent expansion, and no-answer handling."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 1000},
                "project": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 200,
                    "description": "Detected portal project key, normally the Git repository name.",
                },
                "purpose": {
                    "type": "string",
                    "enum": ["failure", "architecture", "decision", "repository", "general"],
                    "default": "general",
                },
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                "max_chars": {
                    "type": "integer",
                    "minimum": 1000,
                    "maximum": 8000,
                    "default": 6000,
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "get_source",
        "title": "Verify one retrieved source chunk",
        "description": (
            "Fetch exactly one current source chunk by the document and chunk IDs returned by "
            "retrieve_context. Use only when exact source verification is needed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "document_id": {"type": "string", "format": "uuid"},
                "chunk_id": {"type": "string", "format": "uuid"},
            },
            "required": ["document_id", "chunk_id"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]

_CLAUSE_SPLIT = re.compile(r"\s*;\s*|[\r\n]+")
_QUERY_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_.+-]{1,39}|[0-9]+[A-Za-z]+|[가-힣]{2,20}")
_QUERY_STOPWORDS = {
    "and",
    "configuration",
    "current",
    "decision",
    "final",
    "for",
    "past",
    "passed",
    "serving",
    "the",
    "what",
    "why",
    "구성",
    "근거",
    "무엇인지",
    "이유",
    "현재",
}


class PortalClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(10.0),
            transport=transport,
        )

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.client.post(path, json=payload)
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self.client.close()


def _string(value: object, field: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return cleaned


def _integer(
    value: object,
    field: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return value


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": rendered}],
        "structuredContent": payload,
    }
    if is_error:
        result["isError"] = True
    return result


def _runtime_mode(client: PortalClient) -> dict[str, Any]:
    try:
        recovery = client.get("/api/v1/embedding/recovery")
    except (httpx.HTTPError, ValueError):
        return {"mode": "unknown", "reason": "runtime_status_unavailable"}
    runtime = recovery.get("runtime") or {}
    if runtime.get("open"):
        return {
            "mode": "keyword-fallback",
            "reason": runtime.get("reason") or "semantic_circuit_open",
        }
    return {"mode": "hybrid", "reason": None}


def _subqueries(query: str) -> list[str]:
    clauses = [item.strip(" .?!") for item in _CLAUSE_SPLIT.split(query) if item.strip(" .?!")]
    return clauses[:3] if len(clauses) > 1 else [query]


def _fallback_terms(query: str) -> list[str]:
    unique: dict[str, str] = {}
    for token in _QUERY_TOKEN.findall(query):
        key = token.casefold()
        if key in _QUERY_STOPWORDS or key in unique:
            continue
        unique[key] = token

    return sorted(unique.values(), key=_term_score, reverse=True)[:2]


def _term_score(token: str) -> tuple[int, int]:
    has_digit = any(char.isdigit() for char in token)
    is_upper = token.isalpha() and token.isupper()
    has_inner_upper = any(char.isupper() for char in token[1:])
    distinctive = 10 if is_upper else 9 if has_inner_upper else 8 if has_digit else 0
    return distinctive, len(token)


def _query_candidates(subquery: str) -> list[str]:
    terms = _fallback_terms(subquery)
    if terms and _term_score(terms[0])[0] > 0:
        return [terms[0]]
    return [subquery]


def _bounded_contexts(
    contexts: list[dict[str, Any]],
    *,
    top_k: int,
    max_chars: int,
) -> tuple[list[dict[str, Any]], int]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for item in contexts:
        if len(selected) >= top_k:
            break
        content = item.get("content")
        provenance = item.get("provenance") or {}
        if not isinstance(content, str) or not content:
            continue
        identity = str(provenance.get("chunk_id") or provenance.get("content_hash") or content)
        if identity in seen:
            continue
        remaining = max_chars - total
        if remaining <= 0:
            break
        bounded = dict(item)
        if len(content) > remaining:
            if remaining < 200:
                break
            bounded["content"] = content[: max(1, remaining - 1)].rstrip() + "…"
            bounded["truncated"] = True
        selected.append(bounded)
        seen.add(identity)
        total += len(bounded["content"])
    return selected, total


def retrieve_context(client: PortalClient, arguments: dict[str, Any]) -> dict[str, Any]:
    query = _string(arguments.get("query"), "query", maximum=1000)
    project_value = arguments.get("project")
    project = (
        _string(project_value, "project", maximum=200) if project_value is not None else None
    )
    purpose = arguments.get("purpose", "general")
    if purpose not in {"failure", "architecture", "decision", "repository", "general"}:
        raise ValueError("purpose is not supported")
    top_k = _integer(arguments.get("top_k"), "top_k", default=5, minimum=1, maximum=10)
    max_chars = _integer(
        arguments.get("max_chars"),
        "max_chars",
        default=6000,
        minimum=1000,
        maximum=8000,
    )
    filters: dict[str, Any] = {}
    if project:
        filters["project"] = project
    started = time.perf_counter()
    planned = _subqueries(query)
    per_query_chars = max(1000, max_chars // len(planned))
    attempted = list(
        dict.fromkeys(candidate for item in planned for candidate in _query_candidates(item))
    )

    def fetch(candidate: str) -> dict[str, Any]:
        return client.post(
                "/api/v1/rag/context",
                {
                    "query": candidate,
                    "top_k": top_k,
                    "max_chars": per_query_chars,
                    "filters": filters,
                },
            )

    with ThreadPoolExecutor(max_workers=min(3, len(attempted))) as executor:
        responses = list(executor.map(fetch, attempted))
    raw_contexts: list[dict[str, Any]] = []
    confidences: list[str] = []
    for response in responses:
        contexts = response.get("context")
        if isinstance(contexts, list) and contexts:
            raw_contexts.extend(item for item in contexts if isinstance(item, dict))
            confidences.append(str(response.get("confidence") or "low"))
    api_calls = len(responses)
    contexts, context_chars = _bounded_contexts(
        raw_contexts,
        top_k=top_k,
        max_chars=max_chars,
    )
    confidence = "high" if "high" in confidences else "low" if contexts else "none"
    runtime = _runtime_mode(client)
    if runtime["mode"] != "hybrid" and confidence == "high":
        confidence = "low"
    navigation: list[dict[str, Any]] = []
    if confidence == "low":
        for item in contexts:
            content = str(item.get("content") or "")
            navigation.append(
                {
                    "snippet": content[:240].rstrip() + ("…" if len(content) > 240 else ""),
                    "retrieval_score": item.get("retrieval_score"),
                    "provenance": item.get("provenance") or {},
                }
            )
        contexts = []
        context_chars = 0
    navigation_chars = sum(len(item["snippet"]) for item in navigation)
    return {
        "query": query,
        "project": project,
        "purpose": purpose,
        "confidence": confidence,
        "no_answer": not contexts,
        "navigation_available": bool(navigation),
        "retrieval_runtime": runtime,
        "contexts": contexts,
        "navigation": navigation,
        "metrics": {
            "api_calls": api_calls,
            "subqueries": attempted,
            "context_count": len(contexts),
            "context_chars": context_chars,
            "navigation_count": len(navigation),
            "navigation_chars": navigation_chars,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    }


def get_source(client: PortalClient, arguments: dict[str, Any]) -> dict[str, Any]:
    document_id = _string(arguments.get("document_id"), "document_id", maximum=36)
    chunk_id = _string(arguments.get("chunk_id"), "chunk_id", maximum=36)
    UUID(document_id)
    UUID(chunk_id)
    for page in range(1, 6):
        document = client.get(
            f"/api/v1/documents/{document_id}",
            params={"chunk_page": page, "chunk_page_size": 200},
        )
        chunks = document.get("chunks") or []
        selected = next(
            (item for item in chunks if isinstance(item, dict) and item.get("id") == chunk_id),
            None,
        )
        if selected is not None:
            return {
                "document": {
                    "id": document.get("id"),
                    "project": document.get("project"),
                    "canonical_path": document.get("canonical_path"),
                    "relative_path": document.get("relative_path"),
                    "content_hash": document.get("content_hash"),
                    "current_version_id": document.get("current_version_id"),
                },
                "chunk": selected,
            }
        if page * 200 >= int(document.get("chunk_total") or 0):
            break
    raise ValueError("chunk is not part of the current document version")


def call_tool(client: PortalClient, name: object, arguments: object) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        return _tool_result({"error": "arguments must be an object"}, is_error=True)
    try:
        if name == "retrieve_context":
            return _tool_result(retrieve_context(client, arguments))
        if name == "get_source":
            return _tool_result(get_source(client, arguments))
        return _tool_result({"error": f"unknown tool: {name}"}, is_error=True)
    except (ValueError, httpx.HTTPError) as exc:
        return _tool_result(
            {"error": type(exc).__name__, "message": str(exc)[:500]},
            is_error=True,
        )


def handle_message(client: PortalClient, message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        requested = (message.get("params") or {}).get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": requested or "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": SERVER_INSTRUCTIONS,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params") or {}
        result = call_tool(client, params.get("name"), params.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> int:
    client = PortalClient()
    try:
        for raw in sys.stdin:
            try:
                message = json.loads(raw.lstrip("\ufeff"))
                if not isinstance(message, dict):
                    raise ValueError("JSON-RPC message must be an object")
                response = handle_message(client, message)
            except (json.JSONDecodeError, ValueError) as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": str(exc)[:500]},
                }
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")))
                sys.stdout.write("\n")
                sys.stdout.flush()
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
