from types import SimpleNamespace

from lkp.search import classify_confidence, lexical_seed_chunk_ids, relaxed_lexical_query


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


def test_seeded_vector_expansion_uses_distinct_evidence_documents_only():
    rows = [
        {
            "chunk_id": "verified-a",
            "document_id": "doc-a",
            "relative_path": "docs/decision.md",
            "tags": [],
            "lexical_rank": 0.4,
            "path_match": 0,
            "symbol_match": 0,
        },
        {
            "chunk_id": "same-document",
            "document_id": "doc-a",
            "relative_path": "docs/decision.md",
            "tags": [],
            "lexical_rank": 0.3,
            "path_match": 0,
            "symbol_match": 0,
        },
        {
            "chunk_id": "reported",
            "document_id": "doc-b",
            "relative_path": "_generated/Projects/demo/Journal/turn.md",
            "tags": ["lifecycle:current"],
            "lexical_rank": 0.9,
            "path_match": 0,
            "symbol_match": 0,
        },
        {
            "chunk_id": "verified-c",
            "document_id": "doc-c",
            "relative_path": "src/runtime.py",
            "tags": [],
            "lexical_rank": 0.2,
            "path_match": 0,
            "symbol_match": 0,
        },
    ]

    assert lexical_seed_chunk_ids(rows) == ["verified-a", "verified-c"]
