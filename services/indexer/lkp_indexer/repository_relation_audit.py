from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from lkp.db import SessionLocal
from sqlalchemy import text

from .repository_analysis.discovery import decode_source_bytes
from .repository_analysis.domain import SourceFile, SourceRelation, SourceSymbol
from .repository_analysis.graph import GraphEdge, GraphNode, build_repository_graph

_SAMPLE_SEED = "repository-relation-audit-v1"
_REGEX_FALSE_CALLS = {"synchronized", "catch", "if", "for", "while", "switch"}


def _stable_key(value: object) -> str:
    return hashlib.sha256(f"{_SAMPLE_SEED}:{value}".encode()).hexdigest()


def _snapshot_rows(session: Any, requested: list[uuid.UUID]) -> list[dict[str, Any]]:
    if requested:
        statement = """
            SELECT s.id AS snapshot_id, p.canonical_name, p.local_source_reference,
                   s.source_hash, s.created_at
            FROM repository_snapshot s
            JOIN repository_project p ON p.id = s.project_id
            WHERE s.id = ANY(CAST(:snapshot_ids AS uuid[]))
            ORDER BY p.canonical_name
        """
        params = {"snapshot_ids": [str(item) for item in requested]}
    else:
        statement = """
            SELECT snapshot_id, canonical_name, local_source_reference,
                   source_hash, created_at
            FROM (
              SELECT DISTINCT ON (p.id)
                     s.id AS snapshot_id, p.id AS project_id, p.canonical_name,
                     p.local_source_reference, s.source_hash, s.created_at
              FROM repository_project p
              JOIN repository_snapshot s ON s.project_id = p.id
              WHERE s.status = 'COMPLETED'
              ORDER BY p.id, s.created_at DESC, s.id DESC
            ) latest
            ORDER BY canonical_name
        """
        params = {}
    return [dict(row) for row in session.execute(text(statement), params).mappings()]


def _facts(
    session: Any,
    snapshot_id: uuid.UUID,
) -> tuple[list[SourceFile], list[SourceSymbol], list[SourceRelation]]:
    files = [
        SourceFile(**dict(row))
        for row in session.execute(
            text(
                """
                SELECT relative_path, content_hash, language, module,
                       line_count, size_bytes
                FROM repository_source_file
                WHERE snapshot_id = :snapshot_id
                """
            ),
            {"snapshot_id": snapshot_id},
        ).mappings()
    ]
    symbols = [
        SourceSymbol(**dict(row))
        for row in session.execute(
            text(
                """
                SELECT relative_path, symbol, symbol_type, start_line, end_line,
                       signature_hash, metadata
                FROM repository_source_symbol
                WHERE snapshot_id = :snapshot_id
                """
            ),
            {"snapshot_id": snapshot_id},
        ).mappings()
    ]
    relations = [
        SourceRelation(**dict(row))
        for row in session.execute(
            text(
                """
                SELECT source_symbol, target_symbol, relation_type, provenance,
                       relative_path, line
                FROM repository_source_relation
                WHERE snapshot_id = :snapshot_id
                """
            ),
            {"snapshot_id": snapshot_id},
        ).mappings()
    ]
    return files, symbols, relations


def _relation_rows(
    session: Any,
    snapshots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ids = [str(item["snapshot_id"]) for item in snapshots]
    return [
        dict(row)
        for row in session.execute(
            text(
                """
                SELECT r.id, r.snapshot_id, p.canonical_name, p.local_source_reference,
                       f.language, f.content_hash, r.source_symbol, r.target_symbol,
                       r.relation_type, r.provenance, r.relative_path, r.line
                FROM repository_source_relation r
                JOIN repository_snapshot s ON s.id = r.snapshot_id
                JOIN repository_project p ON p.id = s.project_id
                LEFT JOIN repository_source_file f
                  ON f.snapshot_id = r.snapshot_id
                 AND f.relative_path = r.relative_path
                WHERE r.snapshot_id = ANY(CAST(:snapshot_ids AS uuid[]))
                """
            ),
            {"snapshot_ids": ids},
        ).mappings()
    ]


def _sample(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    strata: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row["canonical_name"]),
            str(row.get("language") or "UNKNOWN"),
            str(row["relation_type"]),
            str(row["provenance"]),
        )
        strata[key].append(row)
    selected: list[dict[str, Any]] = []
    selected_ids: set[uuid.UUID] = set()
    per_stratum = max(1, min(5, limit // max(1, len(strata))))
    for key in sorted(strata):
        candidates = sorted(strata[key], key=lambda item: _stable_key(item["id"]))
        for row in candidates[:per_stratum]:
            selected.append(row)
            selected_ids.add(row["id"])
    remaining = sorted(
        (row for row in rows if row["id"] not in selected_ids),
        key=lambda item: _stable_key(item["id"]),
    )
    selected.extend(remaining[: max(0, limit - len(selected))])
    return selected[:limit]


def _relation_key(row: dict[str, Any]) -> tuple[object, ...]:
    return (
        row["snapshot_id"],
        row["source_symbol"],
        row["target_symbol"],
        row["relation_type"],
        row["provenance"],
        row["relative_path"],
        row["line"],
    )


def _edge_key(edge: GraphEdge) -> tuple[object, ...]:
    evidence = edge.evidence
    return (
        edge.snapshot_id,
        evidence["source_symbol"],
        evidence["target_symbol"],
        edge.relation_type,
        evidence["legacy_provenance"],
        evidence["relative_path"],
        evidence["line"],
    )


def _source_line(row: dict[str, Any]) -> tuple[str | None, str | None]:
    line = row.get("line")
    if not isinstance(line, int) or line < 1:
        return None, "missing_line"
    path = Path(str(row["local_source_reference"])) / str(row["relative_path"])
    try:
        payload = path.read_bytes()
    except (OSError, UnicodeError) as exc:
        return None, f"{type(exc).__name__}"
    if hashlib.sha256(payload).hexdigest() != row.get("content_hash"):
        return None, "source_hash_mismatch"
    lines = decode_source_bytes(payload)[0].splitlines()
    if line > len(lines):
        return None, "line_out_of_bounds"
    return lines[line - 1].strip(), None


def run(
    *,
    snapshot_ids: list[uuid.UUID],
    sample_size: int = 200,
    include_eligible_rows: bool = False,
    eligible_only: bool = False,
) -> dict[str, Any]:
    with SessionLocal() as session:
        snapshots = _snapshot_rows(session, snapshot_ids)
        rows = _relation_rows(session, snapshots)
        duplicate_counts = Counter(_relation_key(row) for row in rows)
        edges_by_key: dict[tuple[object, ...], GraphEdge] = {}
        nodes_by_id: dict[uuid.UUID, GraphNode] = {}
        for snapshot in snapshots:
            selected = snapshot["snapshot_id"]
            files, symbols, relations = _facts(session, selected)
            graph = build_repository_graph(selected, files, symbols, relations)
            edges_by_key.update({_edge_key(item): item for item in graph.edges})
            nodes_by_id.update({item.id: item for item in graph.nodes})

    population = (
        [
            row
            for row in rows
            if (edge := edges_by_key.get(_relation_key(row))) and not edge.navigation_only
        ]
        if eligible_only
        else rows
    )
    sampled = _sample(population, sample_size)

    audited: list[dict[str, Any]] = []
    for row in sampled:
        edge = edges_by_key.get(_relation_key(row))
        line_text, read_error = _source_line(row)
        target_leaf = str(row["target_symbol"]).rsplit(".", 1)[-1]
        line_supported = bool(line_text and target_leaf.casefold() in line_text.casefold())
        obvious_false = bool(
            row.get("language") != "Python"
            and row["relation_type"] == "CALLS"
            and target_leaf.casefold() in _REGEX_FALSE_CALLS
        )
        target = nodes_by_id.get(edge.target_node_id) if edge and edge.target_node_id else None
        audited.append(
            {
                "id": str(row["id"]),
                "project": row["canonical_name"],
                "snapshot_id": str(row["snapshot_id"]),
                "language": row.get("language") or "UNKNOWN",
                "relation_type": row["relation_type"],
                "legacy_provenance": row["provenance"],
                "relative_path": row["relative_path"],
                "line": row["line"],
                "source_symbol": row["source_symbol"],
                "target_symbol": row["target_symbol"],
                "resolution_status": edge.resolution_status if edge else "NOT_BUILT",
                "verification_status": edge.verification_status if edge else "NOT_BUILT",
                "candidate_count": edge.evidence.get("candidate_count") if edge else None,
                "eligible": bool(edge and not edge.navigation_only),
                "target_path": target.relative_path if target else None,
                "target_qualified_symbol": target.qualified_symbol if target else None,
                "self_loop": bool(edge and edge.target_node_id == edge.source_node_id),
                "duplicate_count": duplicate_counts[_relation_key(row)],
                "source_line_supported": line_supported,
                "source_read_error": read_error,
                "obvious_false_edge": obvious_false,
                "source_line": line_text
                if include_eligible_rows and edge and not edge.navigation_only
                else None,
            }
        )

    resolution = Counter(item["resolution_status"] for item in audited)
    verification = Counter(item["verification_status"] for item in audited)
    strata = Counter(
        (item["project"], item["language"], item["relation_type"], item["legacy_provenance"])
        for item in audited
    )
    eligible = [item for item in audited if item["eligible"]]
    eligible_supported = [item for item in eligible if item["source_line_supported"]]
    eligible_current = [item for item in eligible if item["source_read_error"] is None]
    summary: dict[str, Any] = {
        "sample_seed": _SAMPLE_SEED,
        "sample_scope": "eligible_only" if eligible_only else "all_relations",
        "sample_size": len(audited),
        "population_size": len(population),
        "all_relations_population_size": len(rows),
        "snapshots": [
            {
                "project": item["canonical_name"],
                "snapshot_id": str(item["snapshot_id"]),
                "source_hash": item["source_hash"],
                "created_at": item["created_at"].isoformat(),
            }
            for item in snapshots
        ],
        "resolution_status": dict(sorted(resolution.items())),
        "verification_status": dict(sorted(verification.items())),
        "ambiguous": sum(value for key, value in resolution.items() if key == "AMBIGUOUS"),
        "dangling": sum(value for key, value in resolution.items() if key == "UNRESOLVED"),
        "duplicates": sum(item["duplicate_count"] > 1 for item in audited),
        "self_loops": sum(item["self_loop"] for item in audited),
        "obvious_false_edges": sum(item["obvious_false_edge"] for item in audited),
        "eligible_edges": len(eligible),
        "eligible_source_line_supported": len(eligible_supported),
        "eligible_source_line_support_rate": (
            len(eligible_supported) / len(eligible) if eligible else None
        ),
        "eligible_current_source_rows": len(eligible_current),
        "eligible_current_source_support_rate": (
            len(eligible_supported) / len(eligible_current) if eligible_current else None
        ),
        "eligible_stale_or_unreadable_sources": len(eligible) - len(eligible_current),
        "manual_semantic_precision_established": False,
        "source_line_unsupported_rows": [
            {
                "id": item["id"],
                "project": item["project"],
                "relative_path": item["relative_path"],
                "line": item["line"],
                "relation_type": item["relation_type"],
                "target_symbol": item["target_symbol"],
                "resolution_status": item["resolution_status"],
                "source_read_error": item["source_read_error"],
            }
            for item in eligible
            if not item["source_line_supported"]
        ],
        "strata": [
            {
                "project": key[0],
                "language": key[1],
                "relation_type": key[2],
                "provenance": key[3],
                "sampled": value,
            }
            for key, value in sorted(strata.items())
        ],
    }
    if include_eligible_rows:
        summary["eligible_rows"] = eligible
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit a deterministic stratified relation sample."
    )
    parser.add_argument("--snapshot-id", action="append", type=uuid.UUID, default=[])
    parser.add_argument("--sample-size", type=int, default=200)
    parser.add_argument("--include-eligible-rows", action="store_true")
    parser.add_argument("--eligible-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                snapshot_ids=args.snapshot_id,
                sample_size=args.sample_size,
                include_eligible_rows=args.include_eligible_rows,
                eligible_only=args.eligible_only,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
