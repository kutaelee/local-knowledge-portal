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


def test_curated_article_is_primary_body_without_duplicating_ticket_fields():
    case = SimpleNamespace(
        id=uuid.uuid4(),
        category="implementation",
        title="근거 기반 자동 편집",
        problem="old problem field",
        symptom="old symptom field",
        root_cause="old cause field",
        solution="old solution field",
    )
    article = (
        "> 검증된 근거만 사용한 요약\n\n"
        "## 상황과 맥락\n\n작업의 맥락을 사람이 읽기 쉽게 설명한다. [E1]"
    )
    rendered = render_case_markdown(
        case,
        revision_number=1,
        evidence=[
            SimpleNamespace(
                evidence_type="test_pass",
                claim="테스트 통과",
                locator="pytest",
                verified_value="passed",
                exit_code=0,
                verified=True,
            )
        ],
        source_hash="b" * 64,
        pipeline_version="test-v2",
        generated_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        revision_content={"article_markdown": article},
    )
    assert article in rendered
    assert "old problem field" not in rendered
    assert "## 검증 근거" in rendered
