from lkp.repository_rag_quality import infer_repository_types, repository_reward_rerank
from lkp_indexer.repository_retrieval_evaluation import (
    _answer_failures,
    _reference_key,
)


def _row(*, title: str, file: str, symbol: str | None, vector: float = 0.55) -> dict:
    return {
        "title": title,
        "summary": "",
        "detail": "",
        "processing_steps": [],
        "components": [],
        "configurations": [],
        "dependencies": [],
        "knowledge_type": "COMPONENT",
        "validation_status": "SOURCE_VERIFIED",
        "vector_similarity": vector,
        "keyword_matches": 0,
        "source_references": [
            {
                "file": file,
                "symbol": symbol,
                "start_line": 10,
                "end_line": 20,
                "source_hash": "a" * 64,
            }
        ],
    }


def test_repository_rerank_prioritizes_exact_reference_symbol() -> None:
    expected = _row(
        title="Document rendering",
        file="apps/web/components/knowledge-views.tsx",
        symbol="DocumentDetail",
        vector=0.45,
    )
    semantic_noise = _row(
        title="Runtime data model",
        file="services/api/lkp/models.py",
        symbol=None,
        vector=0.68,
    )
    ranked = repository_reward_rerank(
        [semantic_noise, expected],
        "What evidence prevents inventing runtime behavior for DocumentDetail?",
        limit=5,
    )
    assert ranked[0] is expected


def test_repository_rerank_rejects_unanchored_semantic_noise() -> None:
    noise = _row(
        title="Unrelated cleanup data",
        file="services/api/lkp/cleanup.py",
        symbol=None,
        vector=0.57,
    )
    assert (
        repository_reward_rerank(
            [noise],
            "frobnicator protocol ZXQ-991 behavior",
            limit=5,
        )
        == []
    )


def test_repository_rerank_uses_component_scope_as_retrieval_evidence() -> None:
    expected = _row(
        title="Runtime behavior",
        file="services/api/lkp/search.py",
        symbol=None,
        vector=0,
    )
    expected["components"] = ["search-api"]
    expected["knowledge_type"] = "ERROR_HANDLING"

    ranked = repository_reward_rerank(
        [expected],
        "search-api error cause",
        limit=5,
    )

    assert ranked == [expected]


def test_repository_rerank_fails_closed_on_invalid_source_reference() -> None:
    invalid = _row(
        title="DocumentDetail",
        file="apps/web/components/knowledge-views.tsx",
        symbol="DocumentDetail",
    )
    invalid["source_references"][0]["source_hash"] = "short"
    assert (
        repository_reward_rerank(
            [invalid],
            "DocumentDetail",
            limit=5,
        )
        == []
    )


def test_troubleshooting_scenario_includes_architecture_context() -> None:
    inferred = infer_repository_types(
        "Distinguish confirmed facts, hypotheses, counter-evidence, "
        "additional data, and the next verification step."
    )

    assert "ARCHITECTURE" in inferred


def test_repository_answer_verifier_accepts_only_retrieved_valid_reference() -> None:
    reference = {
        "file": "services/api/lkp/search.py",
        "symbol": "search",
        "start_line": 250,
        "end_line": 430,
        "source_hash": "b" * 64,
    }
    answer = {
        "confirmed_facts": ["검색은 현재 snapshot의 search 함수에서 수행된다."],
        "hypotheses": [],
        "counter_evidence": [],
        "source_references": [reference],
        "configurations": [],
        "additional_data": [],
        "next_steps": ["해당 줄을 확인한다."],
        "confidence": "HIGH",
    }
    assert (
        _answer_failures(
            answer,
            allowed_references={_reference_key(reference)},
            reference_validator=lambda item: item == reference,
        )
        == []
    )
    answer["source_references"] = [{**reference, "file": "unretrieved.py"}]
    assert "unretrieved_or_unverified_reference" in _answer_failures(
        answer,
        allowed_references={_reference_key(reference)},
        reference_validator=lambda item: True,
    )


def test_repository_answer_verifier_rejects_unsupported_confirmed_fact() -> None:
    reference = {
        "file": "services/api/lkp/search.py",
        "symbol": "search",
        "start_line": 250,
        "end_line": 430,
        "source_hash": "b" * 64,
    }
    answer = {
        "confirmed_facts": ["The satellite launch succeeded on Tuesday."],
        "hypotheses": [],
        "counter_evidence": [],
        "source_references": [reference],
        "configurations": [],
        "additional_data": [],
        "next_steps": ["Inspect the database lease."],
        "confidence": "HIGH",
    }

    failures = _answer_failures(
        answer,
        allowed_references={_reference_key(reference)},
        reference_validator=lambda item: True,
        evidence_texts=["The worker failed because its database lease expired."],
    )

    assert "unsupported_confirmed_fact:0" in failures
