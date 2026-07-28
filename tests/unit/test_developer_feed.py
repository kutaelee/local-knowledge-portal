from lkp.settings import Settings
from lkp_indexer.developer_feed import _bounded_payload
from lkp_indexer.generation import DeveloperFeedDraft


def test_feed_contract_enforces_x_limits_and_reply_thread():
    draft = DeveloperFeedDraft(
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
                "role": "reflection",
                "sentences_ko": [
                    "나는 검색이 많이 말하는 것보다 제때 멈추는 태도에서 더 신뢰를 느낀다.",
                    "내 PC도 오늘의 판단 근거를 내일의 나에게 남기는 작업실에 가까워졌다.",
                ],
                "sentences_en": [
                    "I trust search more when it knows where to stop, not when it "
                    "simply says more.",
                    "My PC feels a little more like a workshop "
                    "that leaves today's reasoning for tomorrow.",
                ],
                "source_ids": ["J1"],
            },
        ]
    )
    assert len(draft.posts) == 4
    assert all(2 <= len(post.sentences_ko) <= 3 for post in draft.posts)
    assert all(2 <= len(post.sentences_en) <= 3 for post in draft.posts)
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


def test_feed_defaults_to_three_hour_gemma_batch():
    settings = Settings()
    assert settings.developer_feed_interval_minutes == 180
    assert settings.developer_feed_daily_hour == 18
    assert settings.developer_feed_model == "gemma4:12b"
    assert settings.developer_feed_persona_version.endswith("-workshop")
    assert settings.developer_feed_temperature == 0.45
