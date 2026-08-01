from uuid import uuid4

from lkp.rag_quality import (
    candidate_limit,
    evidence_level,
    infer_query_scope,
    reward_rerank,
    select_verified_answer,
    terms,
    verify_answer_candidate,
)
from lkp.schemas import Provenance, SearchRequest, SearchResult


def _result(
    *,
    snippet: str,
    project: str = "portal",
    tags: list[str] | None = None,
    lexical_rank: float = 0.2,
    vector_similarity: float | None = None,
    relative_path: str = "docs/runbook.md",
    reasons: list[str] | None = None,
) -> SearchResult:
    return SearchResult(
        title=relative_path.rsplit("/", 1)[-1],
        project=project,
        tags=tags or [],
        heading_or_symbol="Recovery",
        snippet=snippet,
        lexical_rank=lexical_rank,
        vector_similarity=vector_similarity,
        fused_rank=1 / 61,
        match_reason=reasons or ["full-text match"],
        provenance=Provenance(
            document_id=uuid4(),
            document_version_id=uuid4(),
            chunk_id=uuid4(),
            source_root="fixture",
            canonical_path=f"/fixture/{relative_path}",
            relative_path=relative_path,
            start_line=1,
            end_line=5,
            content_hash="a" * 64,
            indexed_timestamp="2026-07-27T00:00:00+00:00",
        ),
    )


def test_scope_infers_exact_project_and_failure_case_tags() -> None:
    scope = infer_query_scope(
        "local-knowledge-portal timeout 복구 원인을 알려줘",
        ["portal", "local-knowledge-portal", "other"],
    )
    assert scope.project == "local-knowledge-portal"
    assert scope.inferred_project is True
    assert scope.intent == "failure"
    assert scope.preferred_tags == ("case:error_resolution", "case:operations")


def test_candidate_generation_stays_wide_for_small_top_k() -> None:
    assert candidate_limit(1) == 80
    assert candidate_limit(5) == 80
    assert candidate_limit(10) == 100
    assert candidate_limit(100) == 200


def test_scope_does_not_force_one_project_for_cross_project_query() -> None:
    scope = infer_query_scope(
        "compare alpha-service and beta-service timeout handling",
        ["alpha-service", "beta-service"],
    )
    assert scope.project is None
    assert scope.inferred_project is False


def test_korean_particles_do_not_hide_project_and_domain_terms() -> None:
    assert {"esb", "요청", "처리"}.issubset(terms("ESB가 요청을 처리하는 구조"))


def test_reward_rerank_fails_closed_on_project_scope_violation() -> None:
    request = SearchRequest(
        query="portal worker timeout recovery",
        project="portal",
        mode="keyword",
    )
    scope = infer_query_scope(request.query, ["portal"])
    wrong = _result(
        project="other",
        snippet="portal worker timeout recovery",
    )
    assert reward_rerank([wrong], request, scope) == []


def test_project_scope_comparison_is_case_insensitive_but_not_cross_project() -> None:
    request = SearchRequest(
        query="wedding_picture worker timeout recovery",
        project="Wedding_Picture",
        mode="keyword",
    )
    scope = infer_query_scope(request.query, ["wedding_picture"])
    matching = _result(
        project="wedding_picture",
        snippet="wedding_picture worker timeout recovery",
    )

    assert reward_rerank([matching], request, scope) == [matching]


def test_reward_rerank_rejects_partial_lexical_no_answer_noise() -> None:
    request = SearchRequest(
        query="Mars orbital telemetry retention policy",
        mode="keyword",
    )
    scope = infer_query_scope(request.query, [])
    noise = _result(
        snippet="This document describes a retention policy for local backups.",
        lexical_rank=0.15,
    )
    assert reward_rerank([noise], request, scope) == []


def test_failure_scope_does_not_accept_generic_unrelated_case() -> None:
    request = SearchRequest(
        query="unknown frobnicator protocol error",
        mode="keyword",
    )
    scope = infer_query_scope(request.query, [])
    unrelated = _result(
        snippet="A database worker reported an error and renewed its lease.",
        tags=["case:error_resolution", "lifecycle:verified"],
    )
    assert reward_rerank([unrelated], request, scope) == []


def test_same_symptom_different_cause_prefers_query_specific_cause() -> None:
    request = SearchRequest(
        query="worker timeout caused by expired database lease",
        mode="keyword",
        top_k=2,
    )
    scope = infer_query_scope(request.query, [])
    lease = _result(
        snippet=(
            "Worker timeout failure. Root cause was an expired database lease. "
            "Renew the lease before the long transaction."
        ),
        tags=["case:error_resolution", "lifecycle:verified"],
    )
    network = _result(
        snippet=(
            "Worker timeout failure. Root cause was a network proxy reset. Restart the proxy."
        ),
        tags=["case:error_resolution", "lifecycle:verified"],
    )
    ranked = reward_rerank([network, lease], request, scope)
    assert ranked[0].provenance.chunk_id == lease.provenance.chunk_id


def test_reported_project_journal_is_navigation_not_answer_evidence() -> None:
    reported = _result(
        snippet="작업자가 성공했다고 보고했다.",
        tags=["lifecycle:current"],
        relative_path="_generated/Projects/demo/Journal/turn.md",
    )
    assert evidence_level(reported) == "reported"


def test_claim_verifier_rejects_number_missing_from_citation() -> None:
    evidence_id = str(uuid4())
    contexts = [
        {
            "content": "The batch completed successfully.",
            "evidence_level": "verified",
            "provenance": {"evidence_id": evidence_id},
        }
    ]
    candidate = {
        "text": "The batch completed 400 items successfully.",
        "claims": [
            {
                "text": "The batch completed 400 items successfully.",
                "citations": [evidence_id],
            }
        ],
    }
    verification = verify_answer_candidate(candidate, contexts)
    assert verification["valid"] is False
    assert verification["failures"] == ["claim_number_unsupported:0"]


def test_claim_verifier_rejects_unclaimed_answer_content() -> None:
    evidence_id = str(uuid4())
    contexts = [
        {
            "content": "The worker failed because its database lease expired.",
            "evidence_level": "verified",
            "provenance": {"evidence_id": evidence_id},
        }
    ]
    candidate = {
        "text": "The database lease expired and 400 retries succeeded.",
        "claims": [
            {
                "text": "The database lease expired.",
                "citations": [evidence_id],
            }
        ],
    }

    verification = verify_answer_candidate(candidate, contexts)

    assert verification["valid"] is False
    assert "answer_number_unclaimed" in verification["failures"]


def test_verified_answer_prefers_stronger_evidence_score() -> None:
    source_id = str(uuid4())
    verified_id = str(uuid4())
    contexts = [
        {
            "content": "The worker failed because its database lease expired.",
            "evidence_level": "source",
            "provenance": {"evidence_id": source_id},
        },
        {
            "content": "The worker failed because its database lease expired.",
            "evidence_level": "verified",
            "provenance": {"evidence_id": verified_id},
        },
    ]
    source_candidate = {
        "text": "The database lease expired.",
        "claims": [{"text": "The database lease expired.", "citations": [source_id]}],
    }
    verified_candidate = {
        "text": "The database lease expired.",
        "claims": [{"text": "The database lease expired.", "citations": [verified_id]}],
    }

    selected = select_verified_answer(
        [source_candidate, verified_candidate],
        contexts,
    )

    assert selected["answer"] == verified_candidate


def test_verified_answer_selects_best_candidate_and_repairs_once() -> None:
    chunk_id = str(uuid4())
    contexts = [
        {
            "content": "The worker failed because its database lease expired.",
            "evidence_level": "verified",
            "provenance": {"chunk_id": chunk_id},
        }
    ]
    invalid = {
        "text": "The lease expired.",
        "claims": [{"text": "The database lease expired.", "citations": ["missing"]}],
    }
    calls = []

    def repair(candidate, failures):
        calls.append((candidate, failures))
        return {
            "text": "The lease expired.",
            "claims": [
                {
                    "text": "The database lease expired.",
                    "citations": [chunk_id],
                }
            ],
        }

    selected = select_verified_answer([invalid], contexts, repair=repair)
    assert selected["no_answer"] is False
    assert selected["repaired"] is True
    assert len(calls) == 1


def test_verified_answer_returns_no_answer_after_failed_repair() -> None:
    selected = select_verified_answer(
        [{"text": "Unsupported", "claims": []}],
        [],
        repair=lambda candidate, failures: candidate,
    )
    assert selected["no_answer"] is True
    assert selected["answer"] is None
    assert "claim_missing" in selected["verification"]["failures"]
    assert "no_verified_answer" in selected["verification"]["failures"]


def test_claim_verifier_rejects_reported_navigation_as_grounding() -> None:
    evidence_id = str(uuid4())
    contexts = [
        {
            "content": "The operator reported that the worker completed.",
            "evidence_level": "reported",
            "provenance": {"evidence_id": evidence_id},
        }
    ]
    candidate = {
        "text": "The worker completed.",
        "claims": [
            {
                "text": "The worker completed.",
                "citations": [evidence_id],
            }
        ],
    }

    verification = verify_answer_candidate(candidate, contexts)

    assert verification["valid"] is False
    assert verification["failures"] == ["citation_not_grounding_evidence:0"]
