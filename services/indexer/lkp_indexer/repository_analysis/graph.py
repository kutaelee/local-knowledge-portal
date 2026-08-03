from __future__ import annotations

import hashlib
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from .domain import SourceFile, SourceRelation, SourceSymbol

GRAPH_SCHEMA_VERSION = "repository-graph-v1"
_GRAPH_NAMESPACE = uuid.UUID("bd91987a-5a48-48d5-9fc1-ff3e563e3a90")
_TRUSTED_RESOLUTIONS = {
    "EXACT_LOCAL",
    "EXACT_QUALIFIED",
    "RESOLVED_LOCAL",
    "RESOLVED_IMPORT",
}


@dataclass(frozen=True)
class GraphNode:
    id: uuid.UUID
    snapshot_id: uuid.UUID
    node_type: str
    relative_path: str
    content_hash: str
    qualified_symbol: str
    symbol_leaf: str
    symbol_type: str
    start_line: int
    end_line: int
    signature_hash: str
    node_fingerprint: str


@dataclass(frozen=True)
class GraphEdge:
    id: uuid.UUID
    snapshot_id: uuid.UUID
    source_node_id: uuid.UUID
    target_node_id: uuid.UUID | None
    relation_type: str
    extractor: str
    extractor_version: str
    resolution_status: str
    verification_status: str
    confidence: float
    navigation_only: bool
    edge_fingerprint: str
    evidence: dict[str, object]


@dataclass
class GraphBuildResult:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)


def _digest(*parts: object) -> str:
    payload = "\0".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _leaf(symbol: str) -> str:
    return symbol.rsplit(".", 1)[-1]


def _node_id(snapshot_id: uuid.UUID, fingerprint: str) -> uuid.UUID:
    return uuid.uuid5(_GRAPH_NAMESPACE, f"node:{snapshot_id}:{fingerprint}")


def _edge_id(snapshot_id: uuid.UUID, fingerprint: str) -> uuid.UUID:
    return uuid.uuid5(_GRAPH_NAMESPACE, f"edge:{snapshot_id}:{fingerprint}")


def _file_node(snapshot_id: uuid.UUID, source: SourceFile) -> GraphNode:
    fingerprint = _digest(
        GRAPH_SCHEMA_VERSION,
        "FILE",
        source.relative_path,
        source.content_hash,
    )
    return GraphNode(
        id=_node_id(snapshot_id, fingerprint),
        snapshot_id=snapshot_id,
        node_type="FILE",
        relative_path=source.relative_path,
        content_hash=source.content_hash,
        qualified_symbol=source.relative_path,
        symbol_leaf=PurePosixPath(source.relative_path).name,
        symbol_type="file",
        start_line=1,
        end_line=max(1, source.line_count),
        signature_hash=_digest("file", source.relative_path),
        node_fingerprint=fingerprint,
    )


def _symbol_node(
    snapshot_id: uuid.UUID,
    source: SourceFile,
    symbol: SourceSymbol,
) -> GraphNode:
    fingerprint = _digest(
        GRAPH_SCHEMA_VERSION,
        "SYMBOL",
        symbol.relative_path,
        source.content_hash,
        symbol.symbol,
        symbol.symbol_type,
        symbol.start_line,
        symbol.end_line,
        symbol.signature_hash,
    )
    return GraphNode(
        id=_node_id(snapshot_id, fingerprint),
        snapshot_id=snapshot_id,
        node_type="SYMBOL",
        relative_path=symbol.relative_path,
        content_hash=source.content_hash,
        qualified_symbol=symbol.symbol,
        symbol_leaf=_leaf(symbol.symbol),
        symbol_type=symbol.symbol_type,
        start_line=symbol.start_line,
        end_line=symbol.end_line,
        signature_hash=symbol.signature_hash,
        node_fingerprint=fingerprint,
    )


def _extractor(language: str) -> tuple[str, str, str]:
    if language == "Python":
        return "python-ast", "cpython-ast-v1", "AST_PARSED"
    return "bounded-regex-code", "bounded-regex-v1", "REGEX_INFERRED"


def _import_candidates(
    target: str,
    file_nodes: Iterable[GraphNode],
) -> list[GraphNode]:
    normalized = target.strip(".").replace(".", "/")
    if not normalized:
        return []
    suffixes = {
        normalized,
        f"{normalized}.py",
        f"{normalized}/__init__.py",
        f"{normalized}.ts",
        f"{normalized}.tsx",
        f"{normalized}.js",
        f"{normalized}.java",
    }
    return [
        node
        for node in file_nodes
        if any(
            node.relative_path == suffix or node.relative_path.endswith(f"/{suffix}")
            for suffix in suffixes
        )
    ]


def build_repository_graph(
    snapshot_id: uuid.UUID,
    files: Iterable[SourceFile],
    symbols: Iterable[SourceSymbol],
    relations: Iterable[SourceRelation],
) -> GraphBuildResult:
    """Resolve immutable source facts into a gated graph shadow.

    Parsing proves that text matched an extractor. It does not prove runtime dispatch.
    Regex-derived and unresolved edges are retained for audit/navigation only and can
    never enter graph-guided retrieval's hard-gated candidate set.
    """

    files = list(files)
    symbols = list(symbols)
    relations = list(relations)
    files_by_path = {item.relative_path: item for item in files}
    file_nodes_by_path = {item.relative_path: _file_node(snapshot_id, item) for item in files}
    nodes_by_id: dict[uuid.UUID, GraphNode] = {
        item.id: item for item in file_nodes_by_path.values()
    }
    symbol_nodes: list[GraphNode] = []
    for symbol in symbols:
        source = files_by_path.get(symbol.relative_path)
        if source is None:
            continue
        node = _symbol_node(snapshot_id, source, symbol)
        nodes_by_id[node.id] = node
        symbol_nodes.append(node)

    exact_global: dict[str, list[GraphNode]] = defaultdict(list)
    leaf_global: dict[str, list[GraphNode]] = defaultdict(list)
    exact_path: dict[tuple[str, str], list[GraphNode]] = defaultdict(list)
    leaf_path: dict[tuple[str, str], list[GraphNode]] = defaultdict(list)
    for node in symbol_nodes:
        exact_global[node.qualified_symbol].append(node)
        leaf_global[node.symbol_leaf].append(node)
        exact_path[(node.relative_path, node.qualified_symbol)].append(node)
        leaf_path[(node.relative_path, node.symbol_leaf)].append(node)

    edge_by_fingerprint: dict[str, GraphEdge] = {}
    duplicate_counts: Counter[str] = Counter()
    resolution_counts: Counter[str] = Counter()
    verification_counts: Counter[str] = Counter()
    for relation in relations:
        source_file = files_by_path.get(relation.relative_path)
        if source_file is None:
            continue
        if relation.source_symbol == relation.relative_path:
            source_node = file_nodes_by_path[relation.relative_path]
            source_resolution = "FILE"
        else:
            source_matches = exact_path.get((relation.relative_path, relation.source_symbol), [])
            if len(source_matches) == 1:
                source_node = source_matches[0]
                source_resolution = "EXACT"
            else:
                source_matches = leaf_path.get(
                    (relation.relative_path, _leaf(relation.source_symbol)), []
                )
                if len(source_matches) == 1:
                    source_node = source_matches[0]
                    source_resolution = "RESOLVED_LOCAL"
                else:
                    source_node = file_nodes_by_path[relation.relative_path]
                    source_resolution = "FILE_FALLBACK"

        target_matches = exact_path.get((relation.relative_path, relation.target_symbol), [])
        if len(target_matches) == 1:
            resolution_status = "EXACT_LOCAL"
        elif len(target_matches) > 1:
            resolution_status = "AMBIGUOUS"
        else:
            target_matches = exact_global.get(relation.target_symbol, [])
            if len(target_matches) == 1:
                resolution_status = (
                    "EXACT_QUALIFIED" if "." in relation.target_symbol else "EXACT_UNQUALIFIED"
                )
            elif len(target_matches) > 1:
                resolution_status = "AMBIGUOUS"
            else:
                target_leaf = _leaf(relation.target_symbol)
                local_receiver = relation.target_symbol.startswith(("self.", "this.", "super."))
                local_matches = leaf_path.get((relation.relative_path, target_leaf), [])
                if local_receiver and len(local_matches) == 1:
                    target_matches = local_matches
                    resolution_status = "RESOLVED_LOCAL"
                else:
                    target_matches = leaf_global.get(target_leaf, [])
                    if len(target_matches) == 1:
                        resolution_status = "RESOLVED_UNIQUE"
                    elif len(target_matches) > 1:
                        resolution_status = "AMBIGUOUS"
                    elif relation.relation_type == "IMPORTS":
                        target_matches = _import_candidates(
                            relation.target_symbol,
                            file_nodes_by_path.values(),
                        )
                        if len(target_matches) == 1:
                            resolution_status = "RESOLVED_IMPORT"
                        elif len(target_matches) > 1:
                            resolution_status = "AMBIGUOUS"
                        else:
                            resolution_status = "UNRESOLVED"
                    else:
                        resolution_status = "UNRESOLVED"

        target_node = target_matches[0] if len(target_matches) == 1 else None
        extractor, extractor_version, verification_status = _extractor(source_file.language)
        trusted_resolution = resolution_status in _TRUSTED_RESOLUTIONS
        navigation_only = verification_status == "REGEX_INFERRED" or not trusted_resolution
        confidence = {
            "EXACT_LOCAL": 0.98,
            "EXACT_QUALIFIED": 0.95,
            "EXACT_UNQUALIFIED": 0.65,
            "RESOLVED_LOCAL": 0.88,
            "RESOLVED_UNIQUE": 0.78,
            "RESOLVED_IMPORT": 0.82,
            "AMBIGUOUS": 0.30,
            "UNRESOLVED": 0.15,
        }[resolution_status]
        if verification_status == "REGEX_INFERRED":
            confidence = min(confidence, 0.55)
        fingerprint = _digest(
            GRAPH_SCHEMA_VERSION,
            source_node.id,
            target_node.id if target_node else relation.target_symbol,
            relation.relation_type,
            extractor,
            extractor_version,
            relation.relative_path,
            relation.line,
        )
        duplicate_counts[fingerprint] += 1
        if fingerprint in edge_by_fingerprint:
            continue
        evidence: dict[str, object] = {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "source_hash": source_file.content_hash,
            "relative_path": relation.relative_path,
            "line": relation.line,
            "source_symbol": relation.source_symbol,
            "target_symbol": relation.target_symbol,
            "source_resolution": source_resolution,
            "legacy_provenance": relation.provenance,
            "candidate_node_ids": [str(item.id) for item in target_matches[:16]],
            "candidate_count": len(target_matches),
        }
        edge_by_fingerprint[fingerprint] = GraphEdge(
            id=_edge_id(snapshot_id, fingerprint),
            snapshot_id=snapshot_id,
            source_node_id=source_node.id,
            target_node_id=target_node.id if target_node else None,
            relation_type=relation.relation_type,
            extractor=extractor,
            extractor_version=extractor_version,
            resolution_status=resolution_status,
            verification_status=verification_status,
            confidence=confidence,
            navigation_only=navigation_only,
            edge_fingerprint=fingerprint,
            evidence=evidence,
        )
        resolution_counts[resolution_status] += 1
        verification_counts[verification_status] += 1

    edges = list(edge_by_fingerprint.values())
    return GraphBuildResult(
        nodes=sorted(
            nodes_by_id.values(),
            key=lambda item: (item.relative_path, item.start_line, item.qualified_symbol),
        ),
        edges=sorted(
            edges,
            key=lambda item: (
                str(item.source_node_id),
                item.relation_type,
                item.edge_fingerprint,
            ),
        ),
        metrics={
            "schema_version": GRAPH_SCHEMA_VERSION,
            "nodes": len(nodes_by_id),
            "edges": len(edges),
            "navigation_only_edges": sum(item.navigation_only for item in edges),
            "eligible_edges": sum(not item.navigation_only for item in edges),
            "duplicate_input_edges": sum(value - 1 for value in duplicate_counts.values()),
            "resolution_status": dict(sorted(resolution_counts.items())),
            "verification_status": dict(sorted(verification_counts.items())),
        },
    )
