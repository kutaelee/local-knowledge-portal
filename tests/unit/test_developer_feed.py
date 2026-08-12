import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from lkp.settings import Settings
from lkp_indexer.developer_feed import (
    FeedHistory,
    _bounded_payload,
    _daily_summary_window,
    _duplicate_draft_reason,
    _exact_phrase_candidates,
    _generate_novel_draft,
    _journal_document_filename,
)
from lkp_indexer.generation import DeveloperFeedDraft


def test_feed_contract_enforces_x_limits_and_reply_thread():
    draft = DeveloperFeedDraft(
        publication_kind="troubleshooting",
        technology_or_method="리비전 게이트",
        reader_problem_or_goal="오래된 문서가 최신 근거보다 먼저 나온다",
        outcome_status="not_measured",
        outcome_source_ids=["J1"],
        posts=[
            {
                "role": "observation",
                "sentences_ko": [
                    "검색 결과가 오래된 문서보다 현재 리비전을 먼저 보도록 바꿨다.",
                    "프로젝트 경계가 다른 근거는 같은 결과에 섞이지 않도록 다시 막았다.",
                ],
                "sentences_en": [
                    "Search now puts the current revision ahead of stale documents.",
                    "Evidence from a different project boundary no longer leaks "
                    "into the same result.",
                ],
                "source_ids": ["J1", "D2"],
            },
            {
                "role": "meaning",
                "sentences_ko": [
                    "경계 사례에서는 비슷해 보인다는 이유만으로 답하지 않는다.",
                    "조금 느리더라도 틀린 확신보다 근거 있는 침묵을 선택하도록 했다.",
                ],
                "sentences_en": [
                    "A boundary case no longer gets an answer merely because it looks similar.",
                    "The system now prefers evidence-backed silence to a fast but "
                    "misplaced certainty.",
                ],
                "source_ids": ["J1"],
            },
            {
                "role": "possibility",
                "sentences_ko": [
                    "다음에는 이 경계 점수를 리뷰 큐의 설명에도 활용해볼 수 있다.",
                    "사람이 왜 보류됐는지 한눈에 이해하는 작은 디버깅 지도도 될 듯하다.",
                ],
                "sentences_en": [
                    "Next, this boundary score could explain why an item entered the review queue.",
                    "It might become a small debugging map that helps a person "
                    "understand a hold at a glance.",
                ],
                "source_ids": ["J1"],
            },
            {
                "role": "afterthought",
                "sentences_ko": [
                    "막상 써보니 답이 없는 이유가 보이는 쪽이 괜히 많이 말하는 것보다 편했다.",
                    "다음에 비슷한 버그를 만나도 오늘처럼 한참 헤매지는 않을 것 같다.",
                ],
                "sentences_en": [
                    "In practice, seeing why there is no answer feels better than extra noise.",
                    "The next similar bug should involve less staring at the screen and wondering.",
                ],
                "source_ids": ["J1"],
            },
        ]
    )
    assert len(draft.posts) == 4
    assert all(1 <= len(post.sentences_ko) <= 3 for post in draft.posts)
    assert all(1 <= len(post.sentences_en) <= 3 for post in draft.posts)
    assert all(len(post.content_ko) <= 140 for post in draft.posts)
    assert all(len(post.content_en) <= 280 for post in draft.posts)


def test_payload_budget_drops_document_excerpts_before_verified_journal():
    payload = {
        "post_type": "information_update",
        "sources": [
            {"id": "J1", "source_type": "verified_project_journal", "change": "kept"},
            {
                "id": "D2",
                "source_type": "current_embedded_document",
                "excerpts": [{"content": "x" * 5000}],
            },
        ],
    }
    bounded = _bounded_payload(payload, 1000)
    assert [source["id"] for source in bounded["sources"]] == ["J1"]


def test_exact_phrase_candidates_only_copy_bounded_evidence_strings():
    candidates = _exact_phrase_candidates(
        [
            {
                "title": "Repository revision gate",
                "filename": "revision-gate.md",
                "intent": "Prevent stale evidence from answering",
                "passing_verification": [{"command_family": "pytest -q"}],
                "excerpts": [{"heading": "Fail-closed revision check"}],
            }
        ]
    )

    assert candidates == {
        "technology_or_method": [
            "Repository revision gate",
            "revision-gate.md",
            "pytest -q",
            "Fail-closed revision check",
        ],
        "reader_problem_or_goal": [
            "Repository revision gate",
            "Prevent stale evidence from answering",
        ],
    }


def test_payload_budget_compacts_journals_without_dropping_source_identity():
    payload = {
        "post_type": "information_update",
        "sources": [
            {
                "id": f"J{index}",
                "source_type": "verified_project_journal",
                "project": "local-knowledge-portal",
                "change": f"change-{index} " + ("설명 " * 500),
                "resolution": "검증 결과 " * 100,
            }
            for index in range(1, 5)
        ],
    }
    bounded = _bounded_payload(payload, 2400)
    assert len(json.dumps(bounded, ensure_ascii=False)) <= 2400
    assert [source["id"] for source in bounded["sources"]] == [
        "J1",
        "J2",
        "J3",
        "J4",
    ]


def test_feed_defaults_to_three_hour_gemma_batch():
    settings = Settings()
    assert settings.developer_feed_interval_minutes == 180
    assert settings.developer_feed_daily_hour == 18
    assert settings.developer_feed_max_batches_per_run == 8
    assert settings.developer_feed_model == "gemma4:12b"
    assert settings.developer_feed_persona_version == "peer-developer-v10-threaded-sharing"
    assert settings.developer_feed_prompt_version == "developer-feed-v11-novel-source-thread"
    assert settings.developer_feed_recent_thread_limit == 30
    assert settings.developer_feed_topic_similarity_threshold == 0.88
    assert settings.developer_feed_text_similarity_threshold == 0.72
    assert settings.developer_feed_temperature == 0.65


def test_nightly_feed_summarizes_the_previous_local_day():
    settings = Settings(
        developer_feed_daily_hour=0,
        developer_feed_daily_summary_lag_days=1,
        developer_feed_timezone="Asia/Seoul",
    )
    summary_date, start, end = _daily_summary_window(
        settings,
        now=datetime(2026, 7, 29, 15, 30, tzinfo=timezone.utc),
    )

    assert summary_date.isoformat() == "2026-07-29"
    assert start == datetime(2026, 7, 28, 15, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)


def test_observed_change_claim_scope_is_exposed_to_feed_prompt():
    payload = {
        "post_type": "information_update",
        "sources": [
            {
                "id": "J1",
                "source_type": "verified_project_journal",
                "verification_status": "OBSERVED_CHANGE",
                "claim_scope": "observed_change_only",
                "change": "A current embedded file changed.",
            }
        ],
    }

    bounded = _bounded_payload(payload, 4000)

    assert bounded["sources"][0]["claim_scope"] == "observed_change_only"


def _draft(*, technology: str, problem: str) -> DeveloperFeedDraft:
    roles = ("observation", "meaning", "possibility", "afterthought")
    return DeveloperFeedDraft(
        publication_kind="practical_method",
        technology_or_method=technology,
        reader_problem_or_goal=problem,
        outcome_status="not_measured",
        outcome_source_ids=["J1"],
        posts=[
            {
                "role": role,
                "sentences_ko": [f"{index}번째 재현 단계와 적용 경계를 설명해요."],
                "sentences_en": [f"Step {index} explains the reproducible boundary."],
                "source_ids": ["J1"],
            }
            for index, role in enumerate(roles, start=1)
        ],
    )


def test_journal_document_filename_uses_stable_entry_identity():
    journal = SimpleNamespace(
        id=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        occurred_at=datetime(2026, 8, 12, 1, 2, 3, tzinfo=timezone.utc),
    )

    assert _journal_document_filename(journal) == "20260812T010203Z-12345678.md"


def test_topic_gate_rejects_paraphrased_copy_of_recent_lesson():
    settings = Settings()
    history = FeedHistory(
        journal_ids=frozenset(),
        content_hashes=frozenset(),
        topics=(
            {
                "technology_or_method": "프로젝트별 증분 커서",
                "reader_problem_or_goal": "한 프로젝트의 최신 시각이 다른 프로젝트를 건너뛴다",
            },
        ),
        thread_texts=(),
    )
    duplicate = _draft(
        technology="프로젝트별 증분 커서",
        problem="한 프로젝트의 최신 시각이 다른 프로젝트를 건너뛴다",
    )

    assert _duplicate_draft_reason(duplicate, history, settings) == "topic_similarity:1.000"


class _DraftProvider:
    provider = "test-local"
    model = "test-feed-model"

    def __init__(self, drafts):
        self.drafts = list(drafts)
        self.payloads = []

    def write_developer_feed(self, payload, *, prompt_version):
        self.payloads.append((payload, prompt_version))
        return self.drafts.pop(0), "sha256:test-feed-model"


def test_novelty_gate_repairs_once_with_a_different_supported_topic():
    settings = Settings()
    history = FeedHistory(
        journal_ids=frozenset(),
        content_hashes=frozenset(),
        topics=(
            {
                "technology_or_method": "전역 임베딩 커서",
                "reader_problem_or_goal": "새 작업 기록을 놓친다",
            },
        ),
        thread_texts=(),
    )
    provider = _DraftProvider(
        [
            _draft(technology="전역 임베딩 커서", problem="새 작업 기록을 놓친다"),
            _draft(technology="원문 해시 재사용 차단", problem="같은 근거가 다시 게시된다"),
        ]
    )

    draft, digest, rejection = _generate_novel_draft(
        provider,
        {"post_type": "information_update", "sources": []},
        history=history,
        settings=settings,
    )

    assert draft is not None
    assert draft.technology_or_method == "원문 해시 재사용 차단"
    assert digest == "sha256:test-feed-model"
    assert rejection is None
    assert len(provider.payloads) == 2
    assert provider.payloads[0][0]["recent_topics_to_avoid"] == list(history.topics)
    assert "novelty_repair" in provider.payloads[1][0]


def test_novelty_gate_publishes_nothing_after_second_duplicate():
    settings = Settings()
    history = FeedHistory(
        journal_ids=frozenset(),
        content_hashes=frozenset(),
        topics=(
            {
                "technology_or_method": "전역 임베딩 커서",
                "reader_problem_or_goal": "새 작업 기록을 놓친다",
            },
        ),
        thread_texts=(),
    )
    provider = _DraftProvider(
        [
            _draft(technology="전역 임베딩 커서", problem="새 작업 기록을 놓친다"),
            _draft(technology="전역 임베딩 커서", problem="새 작업 기록을 놓친다"),
        ]
    )

    draft, _, rejection = _generate_novel_draft(
        provider,
        {"post_type": "information_update", "sources": []},
        history=history,
        settings=settings,
    )

    assert draft is None
    assert rejection == "topic_similarity:1.000"
    assert len(provider.payloads) == 2
