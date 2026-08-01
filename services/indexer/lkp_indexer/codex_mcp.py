"""Small read-only MCP bridge from Codex to the Local Knowledge Portal."""

from __future__ import annotations

import json
import re
import sys
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import UUID, uuid4

import httpx
from lkp.rag_quality import select_verified_answer, terms
from lkp.redaction import redact_text

SERVER_NAME = "local-knowledge"
SERVER_VERSION = "0.2.0"
DEFAULT_BASE_URL = "http://127.0.0.1:8010"
SERVER_INSTRUCTIONS = (
    "Inspect current source first for exact implementation questions. Call retrieve_context once "
    "for unfamiliar repository architecture, prior decisions, regressions, verified experiments, "
    "or failures where historical evidence can change the answer. Pass the project key and "
    "purpose. "
    "The tool automatically selects current source documents, verified cases, and current "
    "repository-analysis knowledge. High-confidence contexts may support claims; navigation is not "
    "evidence. For a factual answer based on retrieved context, call verify_answer with up to "
    "three "
    "candidates. If it rejects all candidates, repair once and call it one final time; then return "
    "no-answer. Use get_source only when an exact document chunk must be inspected."
)

TOOLS = [
    {
        "name": "retrieve_context",
        "title": "Retrieve verified local knowledge context",
        "description": (
            "Conditionally retrieve a small, provenance-bearing context from the local knowledge "
            "portal. Call for unfamiliar architecture/lifecycle, a prior decision, a regression "
            "or failure, verified experiment/A-B history, or when the user requests portal "
            "evidence. Inspect current source first for exact implementation questions, and do "
            "not call when it already answers the question. The backend "
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
        "name": "verify_answer",
        "title": "Verify claim and citation support",
        "description": (
            "Select the best claim/citation-grounded answer for a prior retrieval. On rejection, "
            "repair once and retry once; a second rejection is a strict no-answer."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "retrieval_id": {"type": "string", "minLength": 36, "maxLength": 36},
                "candidates": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "minLength": 1, "maxLength": 12000},
                            "claims": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 20,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "text": {
                                            "type": "string",
                                            "minLength": 1,
                                            "maxLength": 2000,
                                        },
                                        "citations": {
                                            "type": "array",
                                            "minItems": 1,
                                            "maxItems": 10,
                                            "items": {"type": "string", "minLength": 1},
                                        },
                                    },
                                    "required": ["text", "citations"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["text", "claims"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["retrieval_id", "candidates"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": False,
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
_FAILURE_MARKERS = (
    "error",
    "failure",
    "failed",
    "regression",
    "오류",
    "실패",
    "장애",
    "원인",
    "재현",
)
_ARCHITECTURE_MARKERS = (
    "architecture",
    "structure",
    "lifecycle",
    "request flow",
    "processing flow",
    "아키텍처",
    "구조",
    "라이프사이클",
    "요청 흐름",
    "처리 흐름",
    "뭐하는",
    "무엇을 하는",
)
_DECISION_MARKERS = (
    "decision",
    "tradeoff",
    "why choose",
    "결정",
    "선택",
    "트레이드오프",
    "채택",
)


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
        self.runtime_cache: tuple[float, dict[str, Any]] | None = None
        self.repository_projects: dict[str, dict[str, Any]] | None = None
        self.retrievals: OrderedDict[str, list[dict[str, Any]]] = OrderedDict()
        self.verification_attempts: dict[str, int] = {}

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

    def store_retrieval(self, contexts: list[dict[str, Any]]) -> str:
        retrieval_id = str(uuid4())
        self.retrievals[retrieval_id] = contexts
        self.retrievals.move_to_end(retrieval_id)
        while len(self.retrievals) > 8:
            expired, _ = self.retrievals.popitem(last=False)
            self.verification_attempts.pop(expired, None)
        return retrieval_id


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
    now = time.monotonic()
    if client.runtime_cache is not None and client.runtime_cache[0] > now:
        return client.runtime_cache[1]
    try:
        recovery = client.get("/api/v1/embedding/recovery")
    except (httpx.HTTPError, ValueError):
        return {"mode": "unknown", "reason": "runtime_status_unavailable"}
    runtime = recovery.get("runtime") or {}
    if runtime.get("open"):
        result = {
            "mode": "keyword-fallback",
            "reason": runtime.get("reason") or "semantic_circuit_open",
        }
    else:
        result = {"mode": "hybrid", "reason": None}
    client.runtime_cache = (now + 30, result)
    return result


def _subqueries(query: str) -> list[str]:
    clauses = [item.strip(" .?!") for item in _CLAUSE_SPLIT.split(query) if item.strip(" .?!")]
    bounded: list[str] = []
    for clause in clauses or [query]:
        remaining = re.sub(r"\s+", " ", clause).strip()
        while remaining:
            if len(remaining) <= 500:
                bounded.append(remaining)
                break
            split_at = remaining.rfind(" ", 0, 501)
            split_at = split_at if split_at >= 250 else 500
            bounded.append(remaining[:split_at].rstrip())
            remaining = remaining[split_at:].lstrip()
            if len(bounded) >= 2:
                break
        if len(bounded) >= 2:
            break
    return bounded[:2]


def _infer_purpose(query: str, requested: str) -> str:
    if requested != "general":
        return requested
    normalized = query.casefold()
    if any(marker in normalized for marker in _FAILURE_MARKERS):
        return "failure"
    if any(marker in normalized for marker in _ARCHITECTURE_MARKERS):
        return "architecture"
    if any(marker in normalized for marker in _DECISION_MARKERS):
        return "decision"
    return requested


def _query_candidates(subquery: str) -> list[str]:
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
        identity = str(
            provenance.get("evidence_id")
            or provenance.get("chunk_id")
            or provenance.get("content_hash")
            or content
        )
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


def _repository_project(
    client: PortalClient,
    project: str | None,
    query: str,
) -> dict[str, Any] | None:
    # Refresh on every retrieval. This list is tiny, while a process-lifetime
    # cache made newly analyzed snapshots invisible until Codex restarted.
    payload = client.get("/api/v1/repository-analysis/projects")
    indexed: dict[str, dict[str, Any]] = {}
    for item in payload.get("items") or []:
        if (
            not isinstance(item, dict)
            or item.get("stale") is not False
            or not item.get("snapshot_id")
        ):
            continue
        for key in ("canonical_name", "display_name"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                indexed[value.strip().casefold()] = item
    client.repository_projects = indexed
    if project:
        return client.repository_projects.get(project.casefold())

    normalized_query = re.sub(r"[^a-z0-9가-힣]+", "", query.casefold())
    matches: dict[str, dict[str, Any]] = {}
    for alias, item in client.repository_projects.items():
        normalized_alias = re.sub(r"[^a-z0-9가-힣]+", "", alias)
        if len(normalized_alias) >= 3 and normalized_alias in normalized_query:
            matches[str(item.get("id"))] = item
    return next(iter(matches.values())) if len(matches) == 1 else None


def _valid_repository_references(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    valid: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        start = item.get("start_line")
        end = item.get("end_line")
        if (
            isinstance(item.get("file"), str)
            and len(str(item.get("source_hash") or "")) == 64
            and isinstance(start, int)
            and isinstance(end, int)
            and start >= 1
            and end >= start
        ):
            valid.append(item)
    return valid[:8]


def _repository_contexts(
    client: PortalClient,
    *,
    query: str,
    project: dict[str, Any],
    top_k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = client.get(
        "/api/v1/repository-analysis/search",
        params={
            "q": query,
            "project_id": project["id"],
            "snapshot_id": project["snapshot_id"],
            "mode": "hybrid",
            "limit": top_k,
        },
    )
    contexts: list[dict[str, Any]] = []
    for row in payload.get("items") or []:
        if not isinstance(row, dict):
            continue
        references = _valid_repository_references(row.get("source_references"))
        if not references:
            continue
        item_id = str(row.get("id") or "")
        if not item_id:
            continue
        sections = [
            str(row.get("title") or ""),
            str(row.get("summary") or ""),
            str(row.get("detail") or ""),
        ]
        steps = row.get("processing_steps")
        if isinstance(steps, list) and steps:
            sections.append("처리 단계: " + " / ".join(str(item) for item in steps[:12]))
        content = redact_text("\n\n".join(item for item in sections if item).strip())
        validation = str(row.get("validation_status") or "")
        level = (
            "verified"
            if validation
            in {"SOURCE_VERIFIED", "TEST_VERIFIED", "RUNTIME_VERIFIED", "HUMAN_APPROVED"}
            else "derived"
        )
        evidence_id = f"repository:{item_id}"
        contexts.append(
            {
                "content": content,
                "retrieval_score": row.get("vector_similarity")
                or min(1.0, float(row.get("keyword_matches") or 0) / 4),
                "title": row.get("title"),
                "project": row.get("project"),
                "tags": [
                    f"repository:{row.get('knowledge_type')}",
                    f"validation:{validation}",
                ],
                "match_reason": [
                    f"repository-mode:{payload.get('mode')}",
                    f"knowledge-type:{row.get('knowledge_type')}",
                ],
                "evidence_level": level,
                "_confidence": "high" if level == "verified" else "low",
                "provenance": {
                    "evidence_id": evidence_id,
                    "source_kind": "repository_analysis",
                    "repository_project_id": str(row.get("project_id") or project["id"]),
                    "snapshot_id": str(row.get("snapshot_id") or project["snapshot_id"]),
                    "snapshot_source_hash": str(row.get("source_hash") or ""),
                    "knowledge_item_id": item_id,
                    "validation_status": validation,
                    "source_references": references,
                },
            }
        )
    return contexts, {
        "mode": payload.get("mode"),
        "snapshot_id": project.get("snapshot_id"),
        "result_count": len(contexts),
    }


def _context_selection_score(item: dict[str, Any], query: str, purpose: str) -> float:
    query_terms = terms(query)
    searchable = " ".join(
        [
            str(item.get("title") or ""),
            str(item.get("content") or ""),
            " ".join(str(tag) for tag in item.get("tags") or []),
        ]
    )
    coverage = len(query_terms.intersection(terms(searchable))) / max(1, len(query_terms))
    level = str(item.get("evidence_level") or "derived")
    evidence = {"verified": 1.0, "source": 0.85}.get(level, 0.0)
    raw_retrieval = max(0.0, float(item.get("retrieval_score") or 0.0))
    retrieval = min(1.0, raw_retrieval * 61 if raw_retrieval <= 0.05 else raw_retrieval)
    provenance = item.get("provenance") or {}
    tags = " ".join(str(tag).casefold() for tag in item.get("tags") or [])
    purpose_bonus = 0.0
    if purpose in {"architecture", "repository"} and provenance.get("source_kind") == (
        "repository_analysis"
    ):
        purpose_bonus = 1.0
    elif purpose == "failure" and any(
        marker in tags for marker in ("error", "failure", "troubleshooting", "lifecycle:verified")
    ):
        purpose_bonus = 1.0
    return round(0.48 * coverage + 0.27 * evidence + 0.15 * retrieval + 0.10 * purpose_bonus, 6)


def retrieve_context(client: PortalClient, arguments: dict[str, Any]) -> dict[str, Any]:
    query = _string(arguments.get("query"), "query", maximum=1000)
    project_value = arguments.get("project")
    project = _string(project_value, "project", maximum=200) if project_value is not None else None
    requested_purpose = arguments.get("purpose", "general")
    if requested_purpose not in {
        "failure",
        "architecture",
        "decision",
        "repository",
        "general",
    }:
        raise ValueError("purpose is not supported")
    purpose = _infer_purpose(query, requested_purpose)
    top_k = _integer(arguments.get("top_k"), "top_k", default=5, minimum=1, maximum=10)
    max_chars = _integer(
        arguments.get("max_chars"),
        "max_chars",
        default=6000,
        minimum=1000,
        maximum=8000,
    )
    started = time.perf_counter()
    planned = _subqueries(query)
    per_query_chars = max(1000, max_chars // len(planned))
    attempted = list(
        dict.fromkeys(candidate for item in planned for candidate in _query_candidates(item))
    )
    repository_project: dict[str, Any] | None = None
    if purpose in {"failure", "architecture", "repository"}:
        try:
            repository_project = _repository_project(client, project, query)
        except (httpx.HTTPError, ValueError):
            repository_project = None
    effective_project = project
    if repository_project and isinstance(repository_project.get("canonical_name"), str):
        effective_project = repository_project["canonical_name"]
    filters: dict[str, Any] = {}
    if effective_project:
        filters["project"] = effective_project

    tasks: list[tuple[str, str]] = []
    if purpose in {"architecture", "repository"} and repository_project:
        tasks.extend(("repository", candidate) for candidate in attempted)
    else:
        tasks.extend(("documents", candidate) for candidate in attempted)
        if purpose == "failure" and repository_project:
            tasks.append(("repository", attempted[0]))

    def fetch(task: tuple[str, str]) -> dict[str, Any]:
        kind, candidate = task
        try:
            if kind == "repository" and repository_project:
                contexts, metadata = _repository_contexts(
                    client,
                    query=candidate,
                    project=repository_project,
                    top_k=top_k,
                )
                return {
                    "kind": kind,
                    "query": candidate,
                    "contexts": contexts,
                    "metadata": metadata,
                }
            response = client.post(
                "/api/v1/rag/context",
                {
                    "query": candidate,
                    "top_k": top_k,
                    "max_chars": per_query_chars,
                    "filters": filters,
                },
            )
            contexts: list[dict[str, Any]] = []
            for raw in response.get("context") or []:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                provenance = dict(item.get("provenance") or {})
                if provenance.get("chunk_id"):
                    provenance.setdefault("evidence_id", str(provenance["chunk_id"]))
                item["provenance"] = provenance
                item["content"] = redact_text(str(item.get("content") or ""))
                item.setdefault("evidence_level", "derived")
                item["_confidence"] = str(response.get("confidence") or "low")
                contexts.append(item)
            return {
                "kind": kind,
                "query": candidate,
                "contexts": contexts,
                "metadata": {"confidence": response.get("confidence")},
            }
        except (httpx.HTTPError, ValueError) as exc:
            return {
                "kind": kind,
                "query": candidate,
                "contexts": [],
                "metadata": {"error": type(exc).__name__},
            }

    with ThreadPoolExecutor(max_workers=min(3, max(1, len(tasks)))) as executor:
        responses = list(executor.map(fetch, tasks))
    if (
        purpose in {"architecture", "repository"}
        and repository_project
        and not any(response["contexts"] for response in responses)
    ):
        fallback_tasks = [("documents", candidate) for candidate in attempted]
        with ThreadPoolExecutor(max_workers=min(2, len(fallback_tasks))) as executor:
            fallback_responses = list(executor.map(fetch, fallback_tasks))
        tasks.extend(fallback_tasks)
        responses.extend(fallback_responses)
    raw_contexts: list[dict[str, Any]] = []
    for response in responses:
        raw_contexts.extend(response["contexts"])
    api_calls = len(tasks)
    runtime = _runtime_mode(client)
    supporting: list[dict[str, Any]] = []
    navigation_source: list[dict[str, Any]] = []
    for item in raw_contexts:
        level = str(item.get("evidence_level") or "derived")
        item_confidence = str(item.pop("_confidence", "low"))
        item["selection_score"] = _context_selection_score(item, query, purpose)
        if level in {"verified", "source"} and item_confidence == "high":
            supporting.append(item)
        else:
            navigation_source.append(item)
    supporting.sort(
        key=lambda item: (
            float(item.get("selection_score") or 0),
            item.get("evidence_level") == "verified",
        ),
        reverse=True,
    )
    navigation_source.sort(
        key=lambda item: float(item.get("selection_score") or 0),
        reverse=True,
    )
    contexts, context_chars = _bounded_contexts(
        supporting,
        top_k=top_k,
        max_chars=max_chars,
    )
    confidence = "high" if contexts else "low" if raw_contexts else "none"
    navigation: list[dict[str, Any]] = []
    if not contexts:
        navigation_contexts, _ = _bounded_contexts(
            navigation_source,
            top_k=top_k,
            max_chars=min(max_chars, 2000),
        )
        for item in navigation_contexts:
            content = str(item.get("content") or "")
            navigation.append(
                {
                    "snippet": content[:240].rstrip() + ("…" if len(content) > 240 else ""),
                    "retrieval_score": item.get("retrieval_score"),
                    "evidence_level": item.get("evidence_level"),
                    "provenance": item.get("provenance") or {},
                }
            )
    navigation_chars = sum(len(item["snippet"]) for item in navigation)
    retrieval_id = client.store_retrieval(contexts) if contexts else None
    return {
        "query": query,
        "project": project,
        "effective_project": effective_project,
        "requested_purpose": requested_purpose,
        "purpose": purpose,
        "retrieval_id": retrieval_id,
        "confidence": confidence,
        "no_answer": not contexts,
        "navigation_available": bool(navigation),
        "retrieval_runtime": runtime,
        "contexts": contexts,
        "navigation": navigation,
        "metrics": {
            "api_calls": api_calls,
            "subqueries": attempted,
            "ranges": [kind for kind, _ in tasks],
            "range_results": [response["metadata"] for response in responses],
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


def verify_answer(client: PortalClient, arguments: dict[str, Any]) -> dict[str, Any]:
    retrieval_id = _string(arguments.get("retrieval_id"), "retrieval_id", maximum=36)
    UUID(retrieval_id)
    contexts = client.retrievals.get(retrieval_id)
    if contexts is None:
        raise ValueError("retrieval_id is unknown or expired")
    attempt = client.verification_attempts.get(retrieval_id, 0) + 1
    if attempt > 2:
        raise ValueError("verification already used its single repair attempt")
    candidates = arguments.get("candidates")
    if not isinstance(candidates, list) or not 1 <= len(candidates) <= 3:
        raise ValueError("candidates must contain between one and three answers")
    for candidate_index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise ValueError("each candidate requires text and claims")
        _string(candidate.get("text"), f"candidates[{candidate_index}].text", maximum=12000)
        claims = candidate.get("claims")
        if not isinstance(claims, list) or not 1 <= len(claims) <= 20:
            raise ValueError("each candidate requires between one and twenty claims")
        for claim_index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                raise ValueError("each claim requires text and citations")
            _string(
                claim.get("text"),
                f"candidates[{candidate_index}].claims[{claim_index}].text",
                maximum=2000,
            )
            citations = claim.get("citations")
            if not isinstance(citations, list) or not 1 <= len(citations) <= 10:
                raise ValueError("each claim requires between one and ten citations")
            if not all(isinstance(citation, str) and citation for citation in citations):
                raise ValueError("citations must be evidence ID strings")
    client.verification_attempts[retrieval_id] = attempt
    selected = select_verified_answer(candidates, contexts)
    valid = not selected["no_answer"]
    repair_allowed = not valid and attempt == 1
    return {
        "retrieval_id": retrieval_id,
        "attempt": attempt,
        "status": "verified" if valid else "repair_required" if repair_allowed else "no_answer",
        "answer": selected["answer"] if valid else None,
        "verification": selected["verification"],
        "repair_allowed": repair_allowed,
        "no_answer": not valid and not repair_allowed,
    }


def call_tool(client: PortalClient, name: object, arguments: object) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        return _tool_result({"error": "arguments must be an object"}, is_error=True)
    try:
        if name == "retrieve_context":
            return _tool_result(retrieve_context(client, arguments))
        if name == "verify_answer":
            return _tool_result(verify_answer(client, arguments))
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
