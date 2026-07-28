import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from lkp_indexer.project_journal import _source_hash, journal_title, render_project_journal


def _entry(
    *,
    failures=None,
    category="operations",
    occurred_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    summary="Configuration updated and validation command passed.",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_stop_activity_id=uuid.uuid4(),
        updated_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        verification_status="VERIFIED",
        title="[portal] operational change",
        intent="Improve an operational setting",
        change_summary=summary,
        failures_json=failures or [],
        resolution="Observed retry resolved the timeout.",
        changed_files=["services/api/lkp/main.py"],
        knowledge_references_json=[],
        occurred_at=occurred_at,
        verification_json=[{"command_family": "test", "exit_code": 0}],
        metadata_json={"work_type": category},
        significance_reasons=[],
    )


def test_journal_explains_absence_of_observed_failure_without_inventing_one():
    entry = _entry()

    rendered = render_project_journal(
        "portal",
        [entry],
        source_hash=_source_hash([entry], "test-v1"),
        pipeline_version="test-v1",
        generated_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        content_language="en",
        integrated=False,
    )

    assert "## Failures and resolution" in rendered
    assert "No failed command was observed" in rendered
    assert "Observed retry resolved the timeout." not in rendered


def test_journal_renders_resolution_only_when_failure_has_exit_evidence():
    entry = _entry(failures=[{"command_family": "test", "exit_code": 1}])

    rendered = render_project_journal(
        "portal",
        [entry],
        source_hash=_source_hash([entry], "test-v1"),
        pipeline_version="test-v1",
        generated_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        content_language="en",
        integrated=False,
    )

    assert "- test: exit `1`" in rendered
    assert "Observed retry resolved the timeout." in rendered


def test_journal_title_uses_specific_report_line_not_conversational_prompt():
    title = journal_title(
        "voice",
        intent="응 그렇게 해",
        report=(
            "구현 및 푸시 완료했습니다.\n\n"
            "- 승격 시작 시 안내 음성을 즉시 재생\n"
            "- 테스트 통과"
        ),
        changed_files=["apps/voice.py"],
        failures=False,
        operational=False,
    )

    assert title == "[voice] 승격 시작 시 안내 음성을 즉시 재생"


def test_current_document_keeps_only_latest_source_per_topic():
    older_operations = _entry(
        occurred_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
        summary="Old operation instructions.",
    )
    current_operations = _entry(
        occurred_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        summary="Current operation instructions.",
    )
    current_performance = _entry(
        category="performance",
        occurred_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        summary="Current performance baseline.",
    )
    entries = [older_operations, current_performance, current_operations]

    rendered = render_project_journal(
        "portal",
        entries,
        source_hash=_source_hash(entries, "test-v1"),
        pipeline_version="test-v1",
        generated_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        content_language="ko",
    )

    assert "## 운영과 설정 [1]" in rendered
    assert "## 성능과 안정성 [2]" in rendered
    assert "Current operation instructions." in rendered
    assert "Current performance baseline." in rendered
    assert "Old operation instructions." not in rendered
    assert "## 근거" in rendered
    assert "[[Journal/" in rendered
