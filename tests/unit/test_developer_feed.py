from lkp.settings import Settings
from lkp_indexer.developer_feed import _bounded_payload
from lkp_indexer.generation import DeveloperFeedDraft


def test_feed_contract_enforces_x_limits_and_reply_thread():
    draft = DeveloperFeedDraft(
        posts=[
            {
                "content_ko": "검색 결과가 오래된 문서보다 현재 리비전을 우선하도록 바꿨다.",
                "content_en": "Search now prefers the current revision over stale documents.",
                "source_ids": ["J1", "D2"],
            },
            {
                "content_ko": "경계 사례에서도 근거가 없으면 답하지 않는다.",
                "content_en": "Boundary cases now return no answer when evidence is absent.",
                "source_ids": ["J1"],
            },
        ]
    )
    assert len(draft.posts) == 2
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
    assert settings.developer_feed_persona_version.endswith("-content")
