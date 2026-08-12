"""Compact machine evidence cards shared by the agent API and MCP client."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from .rag_quality import exact_identifiers, terms

SCHEMA_VERSION = "agent-evidence-card-v1"
_TOKEN_PARTS = re.compile(r"[가-힣一-龥ぁ-んァ-ン]|[A-Za-z0-9_]+|[^\s]")
_EVIDENCE_UNIT_BOUNDARY = re.compile(r"(?<=[.!?。！？])\s+|\n+")
_NONSPACE = re.compile(r"\S+")


def estimate_tokens(value: str) -> int:
    """Conservative local estimate used only for evidence budgeting."""

    total = 0
    for part in _TOKEN_PARTS.findall(value):
        if part.isascii() and part.replace("_", "").isalnum():
            total += max(1, math.ceil(len(part) / 4))
        else:
            total += 1
    return max(1, total)


def evidence_text(item: dict[str, Any]) -> str:
    return str(item.get("discriminating_evidence") or item.get("content") or "")


def evidence_identity(item: dict[str, Any]) -> str:
    provenance = item.get("provenance") or item.get("source") or {}
    return str(
        item.get("evidence_id")
        or provenance.get("evidence_id")
        or provenance.get("chunk_id")
        or provenance.get("content_hash")
        or evidence_text(item)
        or ""
    )


def evidence_state_identity(item: dict[str, Any]) -> str:
    """Return a revision-aware fingerprint for session-level evidence reuse.

    Citation IDs intentionally remain stable and human-auditable. Session state
    has a different requirement: a changed project, revision, source hash, or
    evidence body must never be suppressed as if it had already been returned.
    The fingerprint stores no evidence text and no model reasoning.
    """

    provenance = item.get("provenance") or item.get("source") or {}
    body_hash = hashlib.sha256(evidence_text(item).encode("utf-8")).hexdigest()
    state = {
        "project": str(
            item.get("project")
            or provenance.get("project")
            or provenance.get("project_key")
            or ""
        ),
        "evidence_id": evidence_identity(item),
        "revision": str(
            provenance.get("document_version_id")
            or provenance.get("snapshot_id")
            or provenance.get("revision")
            or ""
        ),
        "source_hash": str(
            provenance.get("content_hash")
            or provenance.get("snapshot_source_hash")
            or provenance.get("source_hash")
            or ""
        ),
        "body_hash": body_hash,
    }
    rendered = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "evidence-state:" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def exact_anchors(value: str) -> set[str]:
    """Return exact identifiers that a cost-aware reranker must not discard."""

    return exact_identifiers(value)


def _term_jaccard(left: set[str], right: set[str]) -> float:
    union = left.union(right)
    return len(left.intersection(right)) / len(union) if union else 0.0


def _bounded_text(value: str, *, max_tokens: int) -> str:
    words = value.split()
    bounded: list[str] = []
    used = 0
    for word in words:
        cost = estimate_tokens(word)
        if bounded and used + cost > max_tokens:
            break
        bounded.append(word)
        used += cost
    snippet = " ".join(bounded)
    if len(snippet) < len(value):
        snippet = snippet.rstrip() + "…"
    return snippet


def _bounded_query_window(
    value: str,
    *,
    query_terms: set[str],
    required_anchors: set[str],
    max_tokens: int,
) -> str:
    """Select whole original words around query evidence without splitting anchors."""

    words = list(_NONSPACE.finditer(value))
    if not words or estimate_tokens(value) <= max_tokens:
        return value

    required_indices: set[int] = set()
    for anchor in required_anchors:
        offset = 0
        while True:
            start = value.find(anchor, offset)
            if start < 0:
                break
            end = start + len(anchor)
            required_indices.update(
                index
                for index, word in enumerate(words)
                if word.start() < end and word.end() > start
            )
            offset = end

    focus_indices = {
        index
        for index, word in enumerate(words)
        if terms(word.group(0)).intersection(query_terms)
    }
    selected = set(required_indices)
    used = sum(estimate_tokens(words[index].group(0)) for index in selected)

    for index in sorted(focus_indices.difference(selected)):
        cost = estimate_tokens(words[index].group(0))
        if used + cost <= max_tokens:
            selected.add(index)
            used += cost

    centers = selected or focus_indices or {0}
    remaining = sorted(
        set(range(len(words))).difference(selected),
        key=lambda index: (min(abs(index - center) for center in centers), index),
    )
    for index in remaining:
        cost = estimate_tokens(words[index].group(0))
        if used + cost > max_tokens:
            continue
        selected.add(index)
        used += cost

    if not selected:
        return _bounded_text(value, max_tokens=max_tokens)
    return " ".join(words[index].group(0) for index in sorted(selected))


def query_focused_extract(
    value: str,
    *,
    query: str,
    max_tokens: int,
) -> tuple[str, dict[str, Any]]:
    """Select concise query-bearing sentences without generating new claims.

    This is deliberately extractive: every returned lexical span originates in
    the evidence, so compression cannot invent a fact or break provenance.
    """

    original_tokens = estimate_tokens(value)
    units = [part.strip() for part in _EVIDENCE_UNIT_BOUNDARY.split(value) if part.strip()]
    query_terms = terms(query)
    query_anchors = exact_anchors(query)
    if not units:
        return "", {
            "method": "query-focused-extractive-v1",
            "original_tokens": original_tokens,
            "compressed_tokens": 0,
            "retention_ratio": 0.0,
            "selected_unit_indices": [],
            "query_term_coverage": 0.0,
            "exact_anchor_coverage": 0.0,
            "budget_exceeded_for_atomic_anchor": False,
        }

    ranked: list[tuple[float, int, int, set[str], set[str], str]] = []
    for index, unit in enumerate(units):
        unit_terms = terms(unit)
        unit_anchors = exact_anchors(unit)
        term_coverage = len(query_terms.intersection(unit_terms)) / max(1, len(query_terms))
        anchor_coverage = len(query_anchors.intersection(unit_anchors)) / max(
            1, len(query_anchors)
        )
        value_density = min(1.0, len(unit_anchors) / 3)
        lead_bias = 1 / (index + 1)
        score = (
            0.55 * term_coverage
            + 0.30 * anchor_coverage
            + 0.10 * value_density
            + 0.05 * lead_bias
        )
        ranked.append(
            (score, index, estimate_tokens(unit), unit_terms, unit_anchors, unit)
        )

    ranked.sort(key=lambda item: (item[0], -item[2], -item[1]), reverse=True)
    best_score = ranked[0][0]
    selected: list[tuple[float, int, int, set[str], set[str], str]] = []
    used = 0
    for candidate in ranked:
        score, _index, cost, unit_terms, _unit_anchors, unit = candidate
        if selected and score < max(0.12, best_score * 0.35):
            continue
        redundancy = max(
            (_term_jaccard(unit_terms, item[3]) for item in selected),
            default=0.0,
        )
        if redundancy >= 0.92:
            continue
        if used + cost > max_tokens:
            if selected:
                continue
            unit = _bounded_query_window(
                unit,
                query_terms=query_terms,
                required_anchors=query_anchors.intersection(_unit_anchors),
                max_tokens=max_tokens,
            )
            cost = estimate_tokens(unit)
            candidate = (score, _index, cost, terms(unit), exact_anchors(unit), unit)
        selected.append(candidate)
        used += cost
        if len(selected) >= 3 or used >= max_tokens:
            break

    if not selected:
        fallback = _bounded_text(value, max_tokens=max_tokens)
        selected = [
            (
                0.0,
                0,
                estimate_tokens(fallback),
                terms(fallback),
                exact_anchors(fallback),
                fallback,
            )
        ]

    # Preserve source order so extraction cannot reverse condition and outcome.
    ordered = sorted(selected, key=lambda item: item[1])
    snippet = " ".join(item[5] for item in ordered)
    selected_terms = terms(snippet)
    selected_anchors = exact_anchors(snippet)
    compressed_tokens = estimate_tokens(snippet) if snippet else 0
    return snippet, {
        "method": "query-focused-extractive-v1",
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
        "retention_ratio": round(compressed_tokens / max(1, original_tokens), 6),
        "selected_unit_indices": [item[1] for item in ordered],
        "query_term_coverage": round(
            len(query_terms.intersection(selected_terms)) / max(1, len(query_terms)),
            6,
        ),
        "exact_anchor_coverage": round(
            len(query_anchors.intersection(selected_anchors)) / max(1, len(query_anchors)),
            6,
        ),
        "budget_exceeded_for_atomic_anchor": compressed_tokens > max_tokens,
    }


def compact_evidence_card(
    item: dict[str, Any],
    *,
    query: str = "",
    max_tokens: int = 180,
    query_focused: bool = False,
) -> dict[str, Any]:
    """Return a machine contract without the human document body."""

    content = evidence_text(item)
    if query_focused and query.strip():
        snippet, compression = query_focused_extract(
            content,
            query=query,
            max_tokens=max_tokens,
        )
    else:
        snippet = _bounded_text(content, max_tokens=max_tokens)
        original_tokens = estimate_tokens(content)
        compressed_tokens = estimate_tokens(snippet)
        compression = {
            "method": "leading-span-v1",
            "original_tokens": original_tokens,
            "compressed_tokens": compressed_tokens,
            "retention_ratio": round(compressed_tokens / max(1, original_tokens), 6),
            "selected_unit_indices": [0] if snippet else [],
            "query_term_coverage": None,
            "exact_anchor_coverage": None,
            "budget_exceeded_for_atomic_anchor": False,
        }
    provenance = dict(item.get("provenance") or item.get("source") or {})
    evidence_id = evidence_identity(item)
    query_terms = terms(query)
    matched_terms = sorted(query_terms.intersection(terms(snippet)))[:12]
    references = list(provenance.get("source_references") or [])
    primary_reference = references[0] if references and isinstance(references[0], dict) else {}
    source_kind = str(provenance.get("source_kind") or "document")
    relative_path = (
        provenance.get("relative_path")
        or provenance.get("canonical_path")
        or primary_reference.get("file")
    )
    title = str(item.get("title") or item.get("heading_or_symbol") or "").strip()
    claim = next(
        (part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", snippet) if part.strip()),
        title or snippet,
    )
    evidence_level = str(item.get("evidence_level") or "derived")
    confidence = str(item.get("confidence") or item.get("_confidence") or "low")
    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_id": evidence_id,
        "project": item.get("project"),
        "label": title or None,
        "claim": claim,
        "relevance": {
            "score": float(item.get("selection_score") or item.get("retrieval_score") or 0.0),
            "matched_terms": matched_terms,
        },
        "source": {
            "kind": source_kind,
            "document_id": provenance.get("document_id"),
            "document_version_id": provenance.get("document_version_id"),
            "chunk_id": provenance.get("chunk_id"),
            "repository_project_id": provenance.get("repository_project_id"),
            "snapshot_id": provenance.get("snapshot_id"),
            "knowledge_item_id": provenance.get("knowledge_item_id"),
            "source_root": provenance.get("source_root"),
            "canonical_path": provenance.get("canonical_path"),
            "path": relative_path,
            "start_line": provenance.get("start_line") or primary_reference.get("start_line"),
            "end_line": provenance.get("end_line") or primary_reference.get("end_line"),
            "revision": provenance.get("document_version_id") or provenance.get("snapshot_id"),
            "source_hash": provenance.get("content_hash") or provenance.get("snapshot_source_hash"),
            "indexed_at": provenance.get("indexed_timestamp"),
            "references": references,
        },
        "verification": {
            "status": "grounding" if evidence_level in {"source", "verified"} else "navigation",
            "evidence_level": evidence_level,
            "confidence": confidence,
            "current": item.get("current", True),
            "revision_match": item.get("revision_match", True),
        },
        "discriminating_evidence": snippet,
        "counter_evidence": list(item.get("counter_evidence") or []),
        "applicability_limits": list(item.get("applicability_limits") or []),
        "next_action": (
            {
                "action": "inspect_current_source_at_repository_reference",
                "tool": None,
                "reason": (
                    "repository analysis is navigation until the referenced source is checked"
                ),
            }
            if source_kind == "repository_analysis"
            else {
                "action": "inspect_exact_current_document_span",
                "tool": "get_source",
                "reason": "inspect_exact_current_span_if_the_claim_requires_more_detail",
            }
        ),
        "estimated_tokens": estimate_tokens(snippet),
        "compression": compression,
        "compact": True,
    }
