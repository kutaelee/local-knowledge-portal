from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.orm import Session

from .domain import SourceFile, SourceRelation, SourceSymbol
from .graph import GraphBuildResult, build_repository_graph


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class RepositoryGraphStore:
    """Persist the rebuildable graph shadow without changing legacy facts."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def replace(
        self,
        snapshot_id: uuid.UUID,
        *,
        files: Iterable[SourceFile],
        symbols: Iterable[SourceSymbol],
        relations: Iterable[SourceRelation],
    ) -> GraphBuildResult:
        graph = build_repository_graph(snapshot_id, files, symbols, relations)
        self.session.execute(
            text("DELETE FROM repository_graph_edge WHERE snapshot_id = :snapshot_id"),
            {"snapshot_id": snapshot_id},
        )
        self.session.execute(
            text("DELETE FROM repository_graph_node WHERE snapshot_id = :snapshot_id"),
            {"snapshot_id": snapshot_id},
        )
        if graph.nodes:
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_graph_node (
                      id, snapshot_id, node_type, relative_path, content_hash,
                      qualified_symbol, symbol_leaf, symbol_type, start_line,
                      end_line, signature_hash, node_fingerprint
                    ) VALUES (
                      :id, :snapshot_id, :node_type, :relative_path, :content_hash,
                      :qualified_symbol, :symbol_leaf, :symbol_type, :start_line,
                      :end_line, :signature_hash, :node_fingerprint
                    )
                    """
                ),
                [asdict(item) for item in graph.nodes],
            )
        if graph.edges:
            edge_rows = []
            for item in graph.edges:
                row = asdict(item)
                row["evidence"] = _json(row["evidence"])
                edge_rows.append(row)
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_graph_edge (
                      id, snapshot_id, source_node_id, target_node_id,
                      relation_type, extractor, extractor_version,
                      resolution_status, verification_status, confidence,
                      navigation_only, edge_fingerprint, evidence
                    ) VALUES (
                      :id, :snapshot_id, :source_node_id, :target_node_id,
                      :relation_type, :extractor, :extractor_version,
                      :resolution_status, :verification_status, :confidence,
                      :navigation_only, :edge_fingerprint, CAST(:evidence AS jsonb)
                    )
                    """
                ),
                edge_rows,
            )
        return graph
