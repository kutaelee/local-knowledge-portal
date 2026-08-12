from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

from lkp.db import SessionLocal
from sqlalchemy import text

from .repository_analysis.domain import SourceFile, SourceRelation, SourceSymbol
from .repository_analysis.graph import build_repository_graph
from .repository_analysis.graph_store import RepositoryGraphStore


def _snapshot_ids(session: Any, requested: uuid.UUID | None) -> list[uuid.UUID]:
    if requested is not None:
        return [requested]
    return list(
        session.execute(
            text(
                """
                SELECT id
                FROM (
                  SELECT DISTINCT ON (project_id) id, project_id, created_at
                  FROM repository_snapshot
                  WHERE status = 'COMPLETED' AND stale = false
                  ORDER BY project_id, created_at DESC, id DESC
                ) latest
                ORDER BY project_id
                """
            )
        ).scalars()
    )


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
                ORDER BY relative_path
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
                ORDER BY relative_path, start_line, symbol
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
                ORDER BY relative_path, line NULLS LAST, source_symbol, target_symbol
                """
            ),
            {"snapshot_id": snapshot_id},
        ).mappings()
    ]
    return files, symbols, relations


def run(*, snapshot_id: uuid.UUID | None = None, apply: bool = False) -> dict[str, Any]:
    output: dict[str, Any] = {"mode": "apply" if apply else "dry-run", "snapshots": []}
    with SessionLocal() as session:
        for selected in _snapshot_ids(session, snapshot_id):
            files, symbols, relations = _facts(session, selected)
            if apply:
                graph = RepositoryGraphStore(session).replace(
                    selected,
                    files=files,
                    symbols=symbols,
                    relations=relations,
                )
            else:
                graph = build_repository_graph(selected, files, symbols, relations)
            output["snapshots"].append(
                {
                    "snapshot_id": str(selected),
                    "input": {
                        "files": len(files),
                        "symbols": len(symbols),
                        "relations": len(relations),
                    },
                    "graph": graph.metrics,
                }
            )
        if apply:
            session.commit()
        else:
            session.rollback()
    return output


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the repository graph shadow; dry-run is the safe default."
    )
    parser.add_argument("--snapshot-id", type=uuid.UUID)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Replace graph shadow rows transactionally (requires migration 0022).",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            run(snapshot_id=args.snapshot_id, apply=args.apply), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
