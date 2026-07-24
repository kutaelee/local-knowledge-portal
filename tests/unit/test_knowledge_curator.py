from lkp.settings import Settings
from lkp_indexer.generation import (
    CuratedKnowledgeArticle,
    EvidenceBoundParagraph,
    _normalize_curated_payload,
)
from lkp_indexer.knowledge import assess_knowledge_value, case_tags
from lkp_indexer.knowledge_curator import (
    GpuSnapshot,
    busy_retry_seconds,
    gpu_is_available,
    qualify_provider,
    run_once,
    validate_draft,
)


def _article(**overrides) -> CuratedKnowledgeArticle:
    supported = [
        EvidenceBoundParagraph(
            text=(
                "마운트 검증은 서비스 준비 상태를 판단하기 전에 두 sentinel 파일을 "
                "직접 확인하도록 구성됐다. 이 설명은 관측된 변경과 테스트 결과만 다룬다."
            ),
            evidence_ids=["E1", "E2"],
        )
    ]
    values = {
        "decision": "publish",
        "category": "implementation",
        "title": "재시작 시 빈 마운트를 차단하는 검증 절차",
        "standfirst": "경로 존재만 보던 검사를 실제 파일 검증으로 강화한 사례다.",
        "standfirst_evidence_ids": ["E1"],
        "context": supported,
        "problem": supported,
        "cause_or_decision": supported,
        "implementation": supported,
        "verification": supported,
        "limitations": [
            EvidenceBoundParagraph(
                text="장시간 절전 복귀는 이번 근거 범위에 포함되지 않았다.",
                evidence_ids=["E2"],
            )
        ],
        "unsupported_inferences": [],
        "decision_reason": "재사용 가능한 구현과 독립 테스트 근거가 있다.",
    }
    values.update(overrides)
    return CuratedKnowledgeArticle(**values)


def test_gpu_policy_and_bounded_backoff():
    settings = Settings(
        knowledge_curation_gpu_min_free_mb=12_288,
        knowledge_curation_gpu_max_utilization=15,
        knowledge_curation_gpu_max_temperature=70,
        knowledge_curation_busy_retry_base_seconds=900,
        knowledge_curation_busy_retry_max_seconds=14_400,
    )
    assert gpu_is_available(GpuSnapshot(32_000, 4_000, 28_000, 5, 45), settings)
    assert not gpu_is_available(GpuSnapshot(32_000, 26_000, 6_000, 5, 45), settings)
    assert not gpu_is_available(GpuSnapshot(32_000, 4_000, 28_000, 80, 45), settings)
    assert busy_retry_seconds(1, settings) == 900
    assert busy_retry_seconds(99, settings) == 14_400


def test_value_harness_keeps_simple_css_change_as_activity_only():
    result = assess_knowledge_value(
        category="implementation",
        problem="버튼 색상을 바꾼다.",
        root_cause="일반적인 화면 스타일 변경이다.",
        solution="버튼 CSS 색상 값을 변경했다.",
        evidence=[
            {
                "evidence_type": "code_change",
                "verified": True,
                "locator": "apps/web/button.css",
                "exit_code": 0,
            },
            {
                "evidence_type": "test_pass",
                "verified": True,
                "locator": "typecheck",
                "exit_code": 0,
            },
        ],
        metadata={"auto_generated": True, "structured_knowledge": False},
    )
    assert result["tier"] == "activity_only"
    assert "presentation_only_change_without_reusable_decision" in result["blockers"]


def test_value_harness_promotes_verified_failure_fix_success_with_explanation():
    result = assess_knowledge_value(
        category="error_resolution",
        problem="준비 상태 검사가 잘못 통과했다.",
        root_cause="필수 sentinel 파일의 존재를 검사하지 않았다.",
        solution="시작 전에 두 sentinel 파일을 모두 검사하도록 수정했다.",
        evidence=[
            {
                "evidence_type": "command_failure",
                "verified": True,
                "exit_code": 1,
            },
            {"evidence_type": "code_change", "verified": True, "exit_code": 0},
            {"evidence_type": "test_pass", "verified": True, "exit_code": 0},
        ],
        metadata={"auto_generated": True, "structured_knowledge": True},
    )
    assert result["tier"] == "promote"
    assert "verified_failure_change_success" in result["signals"]


def test_value_harness_requires_measured_performance_evidence():
    unmeasured = assess_knowledge_value(
        category="performance",
        problem="검색이 느리다.",
        root_cause="부하 원인은 측정되지 않았다.",
        solution="설정을 변경했다.",
        evidence=[{"evidence_type": "code_change", "verified": True, "exit_code": 0}],
    )
    measured = assess_knowledge_value(
        category="performance",
        problem="검색이 느리다.",
        root_cause="필터 선택도가 낮아 후보가 증가했다.",
        solution="필터 순서와 인덱스를 조정했다.",
        evidence=[
            {"evidence_type": item, "verified": True}
            for item in ("performance_before", "performance_after", "load_cause")
        ],
    )
    assert unmeasured["tier"] == "needs_review"
    assert measured["tier"] == "promote"


def test_value_harness_never_reopens_previously_quarantined_activity():
    result = assess_knowledge_value(
        category="implementation",
        problem="구현 목표가 기록됐다.",
        root_cause="구현 방식을 구조화했다.",
        solution="코드 변경과 테스트를 수행했다.",
        evidence=[
            {"evidence_type": "code_change", "verified": True, "exit_code": 0},
            {"evidence_type": "test_pass", "verified": True, "exit_code": 0},
        ],
        metadata={
            "structured_knowledge": True,
            "quality_gate_status": "ACTIVITY_ONLY",
        },
    )
    assert result["tier"] == "activity_only"
    assert "previously_quarantined_activity" in result["blockers"]


def test_case_tags_are_project_scoped_and_multi_dimensional():
    assert case_tags(
        category="performance",
        project="Local Knowledge Portal",
        raw_tags=["platform:WSL2", "Situation:Performance"],
        knowledge_value={"embedding_labels": ["knowledge-value:promote"]},
    ) == [
        "case:performance",
        "knowledge-value:promote",
        "lifecycle:verified",
        "platform:wsl2",
        "project:local-knowledge-portal",
        "situation:performance",
    ]


def test_second_curator_stays_standby_when_database_lock_is_held():
    class LockedResult:
        @staticmethod
        def scalar_one():
            return False

    class LockedSession:
        @staticmethod
        def execute(*_args, **_kwargs):
            return LockedResult()

    def unexpected_gpu_probe():
        raise AssertionError("standby curator must not probe the GPU")

    assert run_once(
        LockedSession(),  # type: ignore[arg-type]
        Settings(),
        gpu_probe=unexpected_gpu_probe,
    ) == {"state": "standby_lock_held"}


def test_evidence_bound_article_passes_without_invention():
    settings = Settings(
        knowledge_curation_min_article_chars=300,
        knowledge_curation_max_article_chars=10_000,
    )
    status, reasons, article = validate_draft(
        _article(),
        {
            "E1": "two sentinel files are checked service_runtime.py",
            "E2": "mount guard tests passed exit_code=0",
        },
        settings,
    )
    assert status == "PASS"
    assert reasons == []
    assert "## 검증된 결과" in article


def test_article_value_is_not_decided_by_a_minimum_character_quota():
    concise = [
        EvidenceBoundParagraph(
            text="검증 근거가 직접 연결된 설명이다.",
            evidence_ids=["E1", "E2"],
        )
    ]
    draft = _article(
        context=concise,
        problem=concise,
        cause_or_decision=concise,
        implementation=concise,
        verification=concise,
        limitations=concise,
    )
    status, reasons, article = validate_draft(
        draft,
        {
            "E1": "verified implementation changed",
            "E2": "verified tests passed exit_code=0",
        },
        Settings(
            knowledge_curation_min_article_chars=5_000,
            knowledge_curation_max_article_chars=10_000,
        ),
    )
    assert len(article) < 5_000
    assert status == "PASS"
    assert reasons == []


def test_model_editorial_recommendation_does_not_own_publish_state():
    draft = _article(decision="needs_review")
    status, reasons, article = validate_draft(
        draft,
        {
            "E1": "two sentinel files are checked service_runtime.py",
            "E2": "mount guard tests passed exit_code=0",
        },
        Settings(),
    )
    assert status == "PASS"
    assert reasons == []
    assert "## 무엇을 어떻게 바꿨나" in article


def test_optional_context_and_limitations_are_not_padded_or_hard_gated():
    draft = _article(
        context=[],
        limitations=[],
        unsupported_inferences=["장기 절전 복귀 결과는 확인되지 않았다."],
    )
    status, reasons, article = validate_draft(
        draft,
        {
            "E1": "two sentinel files are checked service_runtime.py",
            "E2": "mount guard tests passed exit_code=0",
        },
        Settings(),
    )
    assert status == "PASS"
    assert reasons == []
    assert "## 상황과 맥락" not in article
    assert "## 한계와 다음 확인" not in article
    assert "장기 절전 복귀 결과" not in article


def test_explicit_standfirst_evidence_tokens_are_normalized_not_guessed():
    draft = _article(
        standfirst="마운트 검증 변경과 테스트 결과를 정리했다. E1, E2",
        standfirst_evidence_ids=[],
    )
    status, reasons, article = validate_draft(
        draft,
        {
            "E1": "two sentinel files are checked service_runtime.py",
            "E2": "mount guard tests passed exit_code=0",
        },
        Settings(),
    )
    assert status == "PASS"
    assert reasons == []
    assert article.startswith("> 마운트 검증 변경과 테스트 결과를 정리했다. [E1] [E2]")


def test_unsupported_number_is_held_but_style_wording_is_not_a_hard_gate():
    settings = Settings(
        knowledge_curation_min_article_chars=300,
        knowledge_curation_max_article_chars=10_000,
    )
    draft = _article(
        verification=[
            EvidenceBoundParagraph(
                text=(
                    "검사가 완벽하게 동작했고 처리 시간이 99ms로 확인됐다고 기술했지만 "
                    "그 수치는 연결된 실행 근거에 존재하지 않는다."
                ),
                evidence_ids=["E2"],
            )
        ]
    )
    status, reasons, _article_text = validate_draft(
        draft,
        {
            "E1": "two sentinel files are checked",
            "E2": "mount guard tests passed exit_code=0",
        },
        settings,
    )
    assert status == "NEEDS_REVIEW"
    assert "verification_unsupported_number" in reasons
    assert "inflated_language" not in reasons


def test_renderer_owns_citations_and_invalid_evidence_ids_are_held():
    settings = Settings(
        knowledge_curation_min_article_chars=300,
        knowledge_curation_max_article_chars=10_000,
    )
    draft = _article(
        context=[
            EvidenceBoundParagraph(
                text="첫 문단은 확인된 마운트 변경을 설명한다. [invented-label]",
                evidence_ids=["E1"],
            ),
            EvidenceBoundParagraph(
                text="두 번째 문단은 잘못된 근거 식별자를 제출한다.",
                evidence_ids=["E404"],
            ),
        ]
    )
    status, reasons, _article_text = validate_draft(
        draft,
        {
            "E1": "mount guard implementation changed",
            "E2": "mount guard tests passed exit_code=0",
        },
        settings,
    )
    assert status == "NEEDS_REVIEW"
    assert "context_invalid_citation" in reasons
    assert "[invented-label]" not in _article_text
    assert "[E1]" in _article_text


def test_summary_and_paragraph_numbers_must_exist_in_verified_evidence():
    settings = Settings(
        knowledge_curation_min_article_chars=300,
        knowledge_curation_max_article_chars=10_000,
    )
    draft = _article(
        standfirst="근거에는 없는 처리 시간 99ms를 요약에 추가했다.",
        standfirst_evidence_ids=["E1"],
        implementation=[
            EvidenceBoundParagraph(text="처리 시간이 99ms였다.", evidence_ids=["E1", "E2"])
        ],
    )
    status, reasons, _article_text = validate_draft(
        draft,
        {
            "E1": "mount guard implementation changed",
            "E2": "mount guard tests passed exit_code=0",
        },
        settings,
    )
    assert status == "NEEDS_REVIEW"
    assert "standfirst_unsupported_number" in reasons
    assert "implementation_unsupported_number" in reasons


def test_invalid_structured_output_rejects_model_instead_of_retrying_forever():
    class InvalidProvider:
        provider = "ollama"
        model = "gemma4:e4b"

        @staticmethod
        def curate(*_args, **_kwargs):
            CuratedKnowledgeArticle.model_validate(
                {
                    "decision": "publish",
                    "category": "implementation",
                    "decision_reason": "",
                }
            )
            raise AssertionError("validation must fail first")

    report = qualify_provider(
        InvalidProvider(),  # type: ignore[arg-type]
        Settings(knowledge_content_language="ko"),
    )
    assert report["status"] == "FAIL"
    assert len(report["results"]) == 3
    assert report["results"][0]["actual"] == "invalid_structured_output"
    assert report["results"][0]["reasons"] == ["pydantic_validation_failed"]
    assert all(
        item["actual"] == "held" and item["decision"] == "deterministic_harness_hold"
        for item in report["results"][1:]
    )


def test_uncited_extra_paragraphs_are_dropped_without_guessing_evidence():
    payload = _article().model_dump()
    payload["context"].append({"text": "모델이 덧붙인 출처 없는 설명", "evidence_ids": []})
    normalized = _normalize_curated_payload(payload)
    assert len(normalized.context) == 1
    assert normalized.context[0].evidence_ids == ["E1", "E2"]


def test_non_publish_editorial_recommendation_keeps_evidence_bound_content():
    payload = _article(decision="needs_review").model_dump()
    normalized = _normalize_curated_payload(payload)
    assert normalized.decision == "needs_review"
    assert normalized.title
    assert normalized.context
