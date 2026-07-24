from lkp.settings import Settings
from lkp_indexer.generation import CuratedKnowledgeArticle, EvidenceBoundClaim
from lkp_indexer.knowledge_curator import (
    GpuSnapshot,
    busy_retry_seconds,
    gpu_is_available,
    qualify_provider,
    run_once,
    validate_draft,
)


def _article(**overrides) -> CuratedKnowledgeArticle:
    supported = (
        "마운트 검증은 서비스 준비 상태를 판단하기 전에 두 sentinel 파일을 "
        "직접 확인하도록 구성됐다. 이 설명은 관측된 변경과 테스트 결과만 다룬다. [E1] [E2]"
    )
    values = {
        "decision": "publish",
        "category": "implementation",
        "title": "재시작 시 빈 마운트를 차단하는 검증 절차",
        "standfirst": "경로 존재만 보던 검사를 실제 파일 검증으로 강화한 사례다.",
        "context": supported,
        "problem": supported,
        "cause_or_decision": supported,
        "implementation": supported,
        "verification": supported,
        "limitations": "장시간 절전 복귀는 이번 근거 범위에 포함되지 않았다.",
        "evidence_claims": [
            EvidenceBoundClaim(
                text="sentinel 파일을 확인한다.", evidence_ids=["E1", "E2"]
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


def test_article_with_unsupported_number_or_hype_is_held():
    settings = Settings(
        knowledge_curation_min_article_chars=300,
        knowledge_curation_max_article_chars=10_000,
    )
    draft = _article(
        verification=(
            "검사가 완벽하게 동작했고 처리 시간이 99ms로 확인됐다고 기술했지만 "
            "그 수치는 연결된 실행 근거에 존재하지 않는다. [E2]"
        )
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
    assert "inflated_language" in reasons


def test_every_article_paragraph_requires_its_own_evidence_citation():
    settings = Settings(
        knowledge_curation_min_article_chars=300,
        knowledge_curation_max_article_chars=10_000,
    )
    draft = _article(
        context=(
            "첫 문단은 확인된 마운트 변경을 설명한다. [E1]\n\n"
            "두 번째 문단은 별도 근거 인용 없이 새로운 설명을 추가한다."
        )
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
    assert "context_paragraph_missing_citation" in reasons


def test_summary_and_claim_numbers_must_exist_in_verified_evidence():
    settings = Settings(
        knowledge_curation_min_article_chars=300,
        knowledge_curation_max_article_chars=10_000,
    )
    draft = _article(
        standfirst="근거에는 없는 처리 시간 99ms를 요약에 추가했다.",
        evidence_claims=[
            EvidenceBoundClaim(
                text="처리 시간이 99ms였다.", evidence_ids=["E1", "E2"]
            )
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
    assert "claim_unsupported_number" in reasons


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
    assert all(
        item["actual"] == "invalid_structured_output"
        and item["reasons"] == ["pydantic_validation_failed"]
        for item in report["results"]
    )
