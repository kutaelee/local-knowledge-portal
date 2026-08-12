import uuid

from lkp.repository_graph_retrieval import (
    ConservativeByteTokenCounter,
    GraphCandidate,
    _snapshot_is_current,
    classify_graph_intent,
    compose_repository_map,
)
from lkp.settings import Settings
from lkp_indexer.repository_analysis.domain import SourceFile, SourceRelation, SourceSymbol
from lkp_indexer.repository_analysis.graph import build_repository_graph


def _file(path: str, language: str) -> SourceFile:
    return SourceFile(
        relative_path=path,
        content_hash=("a" if language == "Python" else "b") * 64,
        language=language,
        module=None,
        line_count=100,
        size_bytes=1000,
    )


def _symbol(path: str, name: str, line: int) -> SourceSymbol:
    return SourceSymbol(
        relative_path=path,
        symbol=name,
        symbol_type="function",
        start_line=line,
        end_line=line + 2,
        signature_hash=str(line).zfill(64),
        metadata={},
    )


def test_graph_builder_deduplicates_and_gates_regex_edges() -> None:
    snapshot_id = uuid.UUID("c4ddb826-0b0c-4b21-95ba-ed402b0d47e5")
    files = [_file("service.py", "Python"), _file("Legacy.java", "Java")]
    symbols = [
        _symbol("service.py", "Base", 1),
        _symbol("service.py", "Service", 10),
        _symbol("service.py", "Service.run", 20),
        _symbol("Legacy.java", "Legacy", 1),
        _symbol("Legacy.java", "Legacy.run", 10),
    ]
    extends = SourceRelation(
        source_symbol="Service",
        target_symbol="Base",
        relation_type="EXTENDS",
        provenance="STATIC_CONFIRMED",
        relative_path="service.py",
        line=10,
    )
    relations = [
        extends,
        extends,
        SourceRelation(
            source_symbol="Legacy.run",
            target_symbol="Legacy",
            relation_type="CALLS",
            provenance="STATIC_CONFIRMED",
            relative_path="Legacy.java",
            line=12,
        ),
    ]

    first = build_repository_graph(snapshot_id, files, symbols, relations)
    second = build_repository_graph(snapshot_id, files, symbols, relations)

    assert first.metrics["duplicate_input_edges"] == 1
    assert len(first.edges) == 2
    assert [item.id for item in first.nodes] == [item.id for item in second.nodes]
    assert [item.id for item in first.edges] == [item.id for item in second.edges]
    python_edge = next(item for item in first.edges if item.extractor == "python-ast")
    regex_edge = next(item for item in first.edges if item.extractor == "bounded-regex-code")
    assert python_edge.resolution_status == "EXACT_LOCAL"
    assert python_edge.navigation_only is False
    assert regex_edge.verification_status == "REGEX_INFERRED"
    assert regex_edge.navigation_only is True


def test_graph_intent_limits_direct_and_flow_queries() -> None:
    assert classify_graph_intent("Where is Widget configured?") is None
    direct = classify_graph_intent("Who calls Widget.run?")
    flow = classify_graph_intent("Widget.run 변경 영향과 호출 흐름")

    assert direct is not None and direct.max_hops == 1 and direct.include_reverse
    assert flow is not None and flow.max_hops == 2 and flow.include_reverse


def test_repository_map_uses_token_counter_budget() -> None:
    candidate = GraphCandidate(
        node_id=uuid.uuid4(),
        relative_path="src/service.py",
        content_hash="a" * 64,
        qualified_symbol="Service.run",
        symbol_type="method",
        start_line=10,
        end_line=20,
        score=0.9,
        hop=1,
        chain=("Service", "->[CALLS] Service.run"),
    )
    counter = ConservativeByteTokenCounter()
    selected = compose_repository_map([candidate], token_budget=500, counter=counter)
    rejected = compose_repository_map([candidate], token_budget=10, counter=counter)

    assert selected.selected == (candidate,)
    assert selected.token_count == len(selected.text.encode("utf-8"))
    assert rejected.text == ""
    assert rejected.selected == ()


def test_graph_shadow_is_disabled_by_default() -> None:
    assert Settings().repository_graph_shadow_enabled is False


def test_graph_snapshot_gate_fails_closed() -> None:
    class Result:
        def __init__(self, value):
            self.value = value

        def first(self):
            return self.value

    class Database:
        def __init__(self, value):
            self.value = value

        def execute(self, _statement, params):
            assert params["snapshot_id"]
            return Result(self.value)

    snapshot_id = uuid.uuid4()
    assert _snapshot_is_current(Database((1,)), snapshot_id) is True
    assert _snapshot_is_current(Database(None), snapshot_id) is False
