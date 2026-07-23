def reciprocal_rank(rank: int, k: int = 60) -> float:
    return 1 / (k + rank)


def test_rrf_prefers_result_present_in_both_lists():
    both = reciprocal_rank(3) + reciprocal_rank(2)
    lexical_only = reciprocal_rank(1)
    assert both > lexical_only
