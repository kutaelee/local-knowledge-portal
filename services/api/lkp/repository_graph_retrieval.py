from __future__ import annotations

import math
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text

_IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z0-9_.]{2,}\b")
_FLOW_MARKERS = (
    "call flow",
    "data flow",
    "control flow",
    "change impact",
    "lifecycle",
    "processing path",
    "호출 흐름",
    "데이터 흐름",
    "처리 경로",
    "변경 영향",
    "영향 범위",
    "라이프사이클",
)
_RELATION_MARKERS = (
    "call",
    "caller",
    "called by",
    "depend",
    "reference",
    "invoke",
    "호출",
    "의존",
    "참조",
    "연결",
)
_REVERSE_MARKERS = (
    "called by",
    "caller",
    "who calls",
    "reverse",
    "impact",
    "영향",
    "호출하는",
    "누가 호출",
    "참조하는",
)
_STOP_TERMS = {
    "call",
    "caller",
    "called",
    "flow",
    "impact",
    "reference",
    "invoke",
    "what",
    "where",
    "which",
    "how",
    "does",
    "the",
    "this",
}
_RELATION_ALLOWLIST = {
    "CALLS",
    "EXTENDS",
    "IMPORTS",
    "READS_CONFIG",
    "READS_DB",
    "WRITES_DB",
    "SENDS_HTTP",
    "RETRIES",
}
_RESOLUTION_ALLOWLIST = {
    "EXACT_LOCAL",
    "EXACT_QUALIFIED",
    "RESOLVED_LOCAL",
    "RESOLVED_IMPORT",
}


class TokenCounter(Protocol):
    name: str

    def count(self, value: str) -> int: ...


@dataclass(frozen=True)
class ConservativeByteTokenCounter:
    """Fail-safe upper bound for byte-fallback BPE tokenizers.

    Model-specific tokenizers can be injected through the same protocol. The
    shadow default intentionally budgets one UTF-8 byte as one token rather
    than silently converting a character budget into an optimistic token guess.
    """

    name: str = "utf8-byte-upper-bound-v1"

    def count(self, value: str) -> int:
        return len(value.encode("utf-8"))


@dataclass(frozen=True)
class GraphIntent:
    kind: str
    max_hops: int
    include_reverse: bool


@dataclass(frozen=True)
class GraphCandidate:
    node_id: uuid.UUID
    relative_path: str
    content_hash: str
    qualified_symbol: str
    symbol_type: str
    start_line: int
    end_line: int
    score: float
    hop: int
    chain: tuple[str, ...]


@dataclass(frozen=True)
class RepositoryMap:
    text: str
    token_count: int
    token_counter: str
    selected: tuple[GraphCandidate, ...]


def classify_graph_intent(query: str) -> GraphIntent | None:
    normalized = " ".join(query.casefold().split())
    if any(marker in normalized for marker in _FLOW_MARKERS):
        return GraphIntent(
            kind="FLOW_OR_IMPACT",
            max_hops=2,
            include_reverse=any(marker in normalized for marker in _REVERSE_MARKERS),
        )
    if any(marker in normalized for marker in _RELATION_MARKERS):
        return GraphIntent(
            kind="DIRECT_RELATION",
            max_hops=1,
            include_reverse=any(marker in normalized for marker in _REVERSE_MARKERS),
        )
    return None


def _seed_terms(query: str) -> list[str]:
    return sorted(
        {
            value.casefold()
            for value in _IDENTIFIER.findall(query)
            if value.casefold() not in _STOP_TERMS
        },
        key=lambda value: (-len(value), value),
    )[:16]


def _rows(db: Any, statement: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in db.execute(text(statement), params).mappings().all()]


def _seed_nodes(
    db: Any,
    *,
    snapshot_id: uuid.UUID,
    query: str,
    limit: int,
) -> list[dict[str, Any]]:
    terms = _seed_terms(query)
    if not terms:
        return []
    return _rows(
        db,
        """
        SELECT id, relative_path, content_hash, qualified_symbol, symbol_type,
               start_line, end_line,
               CASE
                 WHEN lower(qualified_symbol) = ANY(CAST(:terms AS text[])) THEN 1.0
                 ELSE 0.9
               END AS seed_score
        FROM repository_graph_node
        WHERE snapshot_id = CAST(:snapshot_id AS uuid)
          AND node_type = 'SYMBOL'
          AND (
            lower(qualified_symbol) = ANY(CAST(:terms AS text[])) OR
            lower(symbol_leaf) = ANY(CAST(:terms AS text[]))
          )
        ORDER BY seed_score DESC, length(qualified_symbol) DESC,
                 relative_path, start_line
        LIMIT :limit
        """,
        {"snapshot_id": snapshot_id, "terms": terms, "limit": limit},
    )


def _snapshot_is_current(db: Any, snapshot_id: uuid.UUID) -> bool:
    return (
        db.execute(
            text(
                """
                SELECT 1
                FROM repository_snapshot selected
                WHERE selected.id = CAST(:snapshot_id AS uuid)
                  AND selected.status = 'COMPLETED'
                  AND selected.stale = false
                  AND selected.id = (
                    SELECT latest.id
                    FROM repository_snapshot latest
                    WHERE latest.project_id = selected.project_id
                      AND latest.status = 'COMPLETED'
                      AND latest.stale = false
                    ORDER BY latest.created_at DESC, latest.id DESC
                    LIMIT 1
                  )
                """
            ),
            {"snapshot_id": snapshot_id},
        ).first()
        is not None
    )


def _eligible_edges(
    db: Any,
    *,
    snapshot_id: uuid.UUID,
    node_ids: list[uuid.UUID],
    include_reverse: bool,
    limit: int,
) -> list[dict[str, Any]]:
    if not node_ids:
        return []
    reverse_clause = (
        "OR e.target_node_id = ANY(CAST(:node_ids AS uuid[]))" if include_reverse else ""
    )
    return _rows(
        db,
        f"""
        SELECT e.id, e.source_node_id, e.target_node_id, e.relation_type,
               e.resolution_status, e.verification_status, e.confidence,
               e.evidence,
               source.relative_path AS source_path,
               source.qualified_symbol AS source_symbol,
               target.relative_path AS target_path,
               target.content_hash AS target_hash,
               target.qualified_symbol AS target_symbol,
               target.symbol_type AS target_symbol_type,
               target.start_line AS target_start_line,
               target.end_line AS target_end_line,
               count(*) OVER (PARTITION BY e.source_node_id) AS source_degree,
               count(*) OVER (PARTITION BY e.target_node_id) AS target_degree
        FROM repository_graph_edge e
        JOIN repository_graph_node source ON source.id = e.source_node_id
        JOIN repository_graph_node target ON target.id = e.target_node_id
        WHERE e.snapshot_id = CAST(:snapshot_id AS uuid)
          AND e.target_node_id IS NOT NULL
          AND e.navigation_only = false
          AND e.relation_type = ANY(CAST(:relation_types AS text[]))
          AND e.resolution_status = ANY(CAST(:resolution_statuses AS text[]))
          AND e.verification_status IN ('AST_PARSED', 'RUNTIME_VERIFIED', 'MANUAL_VERIFIED')
          AND (
            e.source_node_id = ANY(CAST(:node_ids AS uuid[]))
            {reverse_clause}
          )
        ORDER BY e.confidence DESC, e.relation_type, e.id
        LIMIT :limit
        """,
        {
            "snapshot_id": snapshot_id,
            "node_ids": [str(item) for item in node_ids],
            "relation_types": sorted(_RELATION_ALLOWLIST),
            "resolution_statuses": sorted(_RESOLUTION_ALLOWLIST),
            "limit": limit,
        },
    )


def graph_candidates(
    db: Any,
    *,
    snapshot_id: uuid.UUID,
    query: str,
    max_fanout: int = 8,
    max_candidates: int = 40,
) -> tuple[GraphIntent | None, list[GraphCandidate]]:
    intent = classify_graph_intent(query)
    if intent is None:
        return None, []
    if not _snapshot_is_current(db, snapshot_id):
        return intent, []
    seeds = _seed_nodes(
        db,
        snapshot_id=snapshot_id,
        query=query,
        limit=min(8, max_candidates),
    )
    if not seeds:
        return intent, []
    candidates: dict[uuid.UUID, GraphCandidate] = {}
    frontier: dict[uuid.UUID, tuple[float, tuple[str, ...]]] = {}
    visited = {item["id"] for item in seeds}
    for seed in seeds:
        node_id = seed["id"]
        seed_chain = (str(seed["qualified_symbol"]),)
        frontier[node_id] = (float(seed["seed_score"]), seed_chain)
        candidates[node_id] = GraphCandidate(
            node_id=node_id,
            relative_path=str(seed["relative_path"]),
            content_hash=str(seed["content_hash"]),
            qualified_symbol=str(seed["qualified_symbol"]),
            symbol_type=str(seed["symbol_type"]),
            start_line=int(seed["start_line"]),
            end_line=int(seed["end_line"]),
            score=float(seed["seed_score"]),
            hop=0,
            chain=seed_chain,
        )
    for hop in range(1, intent.max_hops + 1):
        edges = _eligible_edges(
            db,
            snapshot_id=snapshot_id,
            node_ids=list(frontier),
            include_reverse=intent.include_reverse,
            limit=max_candidates * max_fanout * 2,
        )
        per_parent: dict[uuid.UUID, int] = {}
        next_frontier: dict[uuid.UUID, tuple[float, tuple[str, ...]]] = {}
        for edge in edges:
            source_id = edge["source_node_id"]
            target_id = edge["target_node_id"]
            reverse = source_id not in frontier and target_id in frontier
            parent_id = target_id if reverse else source_id
            child_id = source_id if reverse else target_id
            if parent_id not in frontier or child_id in visited:
                continue
            if per_parent.get(parent_id, 0) >= max_fanout:
                continue
            per_parent[parent_id] = per_parent.get(parent_id, 0) + 1
            parent_score, parent_chain = frontier[parent_id]
            degree = int(edge["target_degree"] if reverse else edge["source_degree"])
            hub_penalty = 1.0 + 0.15 * math.log1p(max(0, degree))
            score = parent_score * float(edge["confidence"]) * (0.82**hop) / hub_penalty
            arrow = "<-" if reverse else "->"
            child_symbol = str(edge["source_symbol"] if reverse else edge["target_symbol"])
            chain = (
                *parent_chain,
                f"{arrow}[{edge['relation_type']}] {child_symbol}",
            )
            child = GraphCandidate(
                node_id=child_id,
                relative_path=str(edge["source_path"] if reverse else edge["target_path"]),
                content_hash=str(
                    edge["evidence"].get("source_hash") if reverse else edge["target_hash"]
                ),
                qualified_symbol=child_symbol,
                symbol_type=str(edge["target_symbol_type"] if not reverse else "symbol"),
                start_line=int(
                    edge["evidence"].get("line") or 1 if reverse else edge["target_start_line"]
                ),
                end_line=int(
                    edge["evidence"].get("line") or 1 if reverse else edge["target_end_line"]
                ),
                score=score,
                hop=hop,
                chain=chain,
            )
            current = candidates.get(child_id)
            if current is None or child.score > current.score:
                candidates[child_id] = child
                next_frontier[child_id] = (score, chain)
            visited.add(child_id)
        frontier = next_frontier
        if not frontier or len(candidates) >= max_candidates:
            break
    ranked = sorted(
        candidates.values(),
        key=lambda item: (item.score, -item.hop, item.qualified_symbol),
        reverse=True,
    )
    return intent, ranked[:max_candidates]


def compose_repository_map(
    candidates: list[GraphCandidate],
    *,
    token_budget: int,
    counter: TokenCounter | None = None,
) -> RepositoryMap:
    counter = counter or ConservativeByteTokenCounter()
    lines = ["repository-map-v1"]
    selected: list[GraphCandidate] = []
    for item in candidates:
        chain = " ".join(item.chain)
        line = (
            f"{item.relative_path}:{item.start_line}-{item.end_line} | "
            f"{item.symbol_type} {item.qualified_symbol} | {chain}"
        )
        candidate_text = "\n".join([*lines, line])
        if counter.count(candidate_text) > token_budget:
            continue
        lines.append(line)
        selected.append(item)
    rendered = "\n".join(lines) if selected else ""
    return RepositoryMap(
        text=rendered,
        token_count=counter.count(rendered),
        token_counter=counter.name,
        selected=tuple(selected),
    )


def run_graph_shadow(
    db: Any,
    *,
    snapshot_id: uuid.UUID,
    query: str,
    baseline_rows: list[dict[str, Any]],
    max_fanout: int,
    max_candidates: int,
    token_budget: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    intent, candidates = graph_candidates(
        db,
        snapshot_id=snapshot_id,
        query=query,
        max_fanout=max_fanout,
        max_candidates=max_candidates,
    )
    repository_map = compose_repository_map(candidates, token_budget=token_budget)
    baseline_paths = {
        str(reference.get("file"))
        for row in baseline_rows
        for reference in row.get("source_references") or []
        if isinstance(reference, dict) and reference.get("file")
    }
    graph_paths = {item.relative_path for item in repository_map.selected}
    return {
        "intent": intent.kind if intent else "NONE",
        "max_hops": intent.max_hops if intent else 0,
        "candidate_count": len(candidates),
        "map_selected": len(repository_map.selected),
        "map_tokens_upper_bound": repository_map.token_count,
        "token_counter": repository_map.token_counter,
        "baseline_path_count": len(baseline_paths),
        "path_overlap": len(baseline_paths & graph_paths),
        "novel_graph_paths": len(graph_paths - baseline_paths),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "promoted": False,
    }
