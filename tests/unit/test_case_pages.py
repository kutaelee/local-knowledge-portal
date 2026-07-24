import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from lkp_indexer.case_pages import render_case_markdown


def test_case_markdown_is_utf8_human_readable_and_current():
    case = SimpleNamespace(
        id=uuid.uuid4(),
        category="operations",
        title="재부팅 후 포털 자동 시작 복구",
        problem="Windows 로그인 후 웹 포털이 열리지 않았다.",
        symptom="예약 작업 결과가 1이고 웹 연결이 거부됐다.",
        root_cause="Docker readiness 실패를 종료 예외로 처리했다.",
        solution="실패를 대기 상태로 처리하고 웹 readiness까지 확인한다.",
    )
    evidence = [
        SimpleNamespace(
            evidence_type="recovery_success",
            claim="예약 작업과 웹 응답을 확인했다.",
            locator="startup.jsonl",
            verified_value="LastTaskResult=0, HTTP 200",
            exit_code=0,
            verified=True,
        )
    ]
    rendered = render_case_markdown(
        case,
        revision_number=2,
        evidence=evidence,
        source_hash="a" * 64,
        pipeline_version="test-v1",
        generated_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
    )
    assert "# 재부팅 후 포털 자동 시작 복구" in rendered
    assert "## 확인된 원인" in rendered
    assert "Docker readiness 실패를 종료 예외로 처리했다." in rendered
    assert "LastTaskResult=0, HTTP 200" in rendered
    assert "case_revision: 2" in rendered
    rendered.encode("utf-8").decode("utf-8")
