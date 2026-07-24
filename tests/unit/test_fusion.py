from types import SimpleNamespace

from lkp.search import classify_confidence, relaxed_lexical_query


def reciprocal_rank(rank: int, k: int = 60) -> float:
    return 1 / (k + rank)


def test_rrf_prefers_result_present_in_both_lists():
    both = reciprocal_rank(3) + reciprocal_rank(2)
    lexical_only = reciprocal_rank(1)
    assert both > lexical_only


def test_semantic_confidence_uses_similarity_not_rrf_position():
    low = [SimpleNamespace(lexical_rank=None, vector_similarity=0.48)]
    high = [SimpleNamespace(lexical_rank=None, vector_similarity=0.72)]
    assert classify_confidence(low, "semantic", high_similarity=0.6) == "low"
    assert classify_confidence(high, "semantic", high_similarity=0.6) == "high"
    assert classify_confidence([], "semantic", high_similarity=0.6) == "none"


def test_relaxed_lexical_query_is_bounded_and_requires_multiple_terms():
    assert relaxed_lexical_query("PostgreSQL queue lease recovery") == (
        '"postgresql" "queue" OR "postgresql" "lease" OR '
        '"postgresql" "recovery" OR "queue" "lease" OR '
        '"queue" "recovery" OR "lease" "recovery"'
    )
    assert relaxed_lexical_query("queue queue lease") == '"queue" "lease"'
    assert relaxed_lexical_query("single") is None
