import runpy
from pathlib import Path

_SCRIPT = Path(__file__).parents[2] / "scripts" / "evaluate_rag_quality.py"
_EVALUATOR = runpy.run_path(str(_SCRIPT))
_bounded_evaluation_query = _EVALUATOR["_bounded_evaluation_query"]
_repository_cases = _EVALUATOR["_repository_cases"]


def test_evaluation_query_preserves_head_and_tail_inside_api_limit() -> None:
    source = "initial-timeout " * 60 + "unique-root-cause-at-tail"

    query, truncated = _bounded_evaluation_query(source)

    assert truncated is True
    assert len(query) <= 500
    assert query.startswith("initial-timeout")
    assert query.endswith("unique-root-cause-at-tail")


def test_short_evaluation_query_is_unchanged_except_whitespace() -> None:
    query, truncated = _bounded_evaluation_query("worker\nlease   expired")

    assert query == "worker lease expired"
    assert truncated is False


def test_stale_repository_snapshot_is_not_scored_as_answerable() -> None:
    class Client:
        def get(self, _path):
            raise AssertionError("stale projects must not request evaluation rows")

    projects = [
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "snapshot_id": "22222222-2222-4222-8222-222222222222",
            "stale": True,
        }
    ]

    assert _repository_cases(Client(), projects) == []
