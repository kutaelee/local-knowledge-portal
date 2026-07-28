from lkp_indexer.developer_feed import (
    _daily_copy,
    _evidence_copy,
    _expand_messages,
    _split_text,
    _work_copy,
)


def test_activity_thread_is_bilingual_and_x_sized():
    messages = [
        _work_copy(
            "local-knowledge-portal",
            7,
            3,
            ["verified_failure_and_recovery"],
        ),
        _evidence_copy(
            [
                "services/indexer/developer_feed.py",
                "services/api/main.py",
                "apps/web/knowledge-views.tsx",
                "apps/web/globals.css",
                "tests/test_developer_feed.py",
            ],
            3,
        ),
    ]
    expanded = _expand_messages(messages)
    assert all(0 < len(ko) <= 140 and 0 < len(en) <= 280 for ko, en in expanded)
    assert "오류를 복구한" in messages[0][0]
    assert "recovered a verified failure" in messages[0][1].casefold()
    assert "외 1개" in messages[1][0]
    assert "+1 more" in messages[1][1]


def test_daily_wrap_is_mandatory_even_without_new_embedded_work():
    ko, en = _daily_copy("2026-07-28", 0, [], 0)
    assert "새로 임베딩되고 검증된 작업은 없었다" in ko
    assert "no newly embedded, verified work" in en
    assert len(ko) <= 140
    assert len(en) <= 280


def test_long_post_becomes_lossless_reply_parts():
    source = " ".join(f"근거{i}" for i in range(80))
    parts = _split_text(source, 140)
    assert len(parts) > 1
    assert all(len(part) <= 140 for part in parts)
    assert " ".join(parts) == source
