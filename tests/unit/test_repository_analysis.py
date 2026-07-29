from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from lkp_indexer.repository_analysis import __main__ as repository_analysis_main
from lkp_indexer.repository_analysis import provider as provider_module
from lkp_indexer.repository_analysis.analyzers import ConfigurationAnalyzer, XmlAnalyzer
from lkp_indexer.repository_analysis.design import _component_key, _humanize
from lkp_indexer.repository_analysis.discovery import discover
from lkp_indexer.repository_analysis.domain import (
    Claim,
    Confidence,
    ConfigurationReference,
    EvidenceReference,
    KnowledgeItem,
    SourceFile,
    ValidationStatus,
)
from lkp_indexer.repository_analysis.pipeline import RepositoryAnalysisPipeline
from lkp_indexer.repository_analysis.provider import LocalModelProvider, ModelInvocation
from lkp_indexer.repository_analysis.report import (
    _claim_catalog,
    synthesize_repository_report,
)
from lkp_indexer.repository_analysis.validator import validate_claim
from lkp_indexer.repository_embedding_reindex import embedding_text
from lkp_indexer.repository_retrieval_evaluation import _expected_rank


@pytest.fixture
def sample_repository(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text(
        """
import json

class Service:
    def handle(self, value):
        return json.dumps(value)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "application.yaml").write_text(
        "service:\n  timeout: 30\n  endpoint: http://localhost\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"fastify": "5.0.0"}}),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("SECRET_TOKEN=never-store-this\n", encoding="utf-8")
    return tmp_path


def test_discovery_is_allowlisted_and_skips_sensitive_files(sample_repository: Path) -> None:
    result = discover(sample_repository, allowed_roots=[sample_repository])

    assert {item.relative_path for item in result.files} == {
        "application.yaml",
        "package.json",
        "src/service.py",
    }
    assert {"path": ".env", "reason": "sensitive_filename"} in result.skipped
    assert all(len(item.content_hash) == 64 for item in result.files)


def test_discovery_rejects_path_outside_allowlist(
    sample_repository: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    other = tmp_path_factory.mktemp("outside")
    with pytest.raises(ValueError, match="allowlist"):
        discover(other, allowed_roots=[sample_repository])


def test_xml_analyzer_parses_escaped_euc_kr_declaration_without_stopping_repository(
    tmp_path: Path,
) -> None:
    content = (
        '<?xml version=\\"1.0\\" encoding=\\"EUC-KR\\"?><beans><bean id="한글서비스"/></beans>'
    ).encode("euc-kr")
    path = tmp_path / "legacy.xml"
    path.write_bytes(content)
    source = SourceFile(
        relative_path=path.name,
        content_hash="a" * 64,
        language="XML",
        module=None,
        line_count=1,
        size_bytes=len(content),
    )

    facts = XmlAnalyzer().analyze(tmp_path, source)

    assert facts.warnings == []
    assert any(item.symbol == "한글서비스" for item in facts.symbols)


def test_configuration_analyzer_normalizes_json_comments_and_string_concatenation(
    tmp_path: Path,
) -> None:
    content = """
{
  /* legacy configuration */
  "message": "첫째 줄" +
             "둘째 줄",
  "endpoint": "http://localhost/service",
  "label": '레거시 값',
}
""".strip()
    path = tmp_path / "legacy.json"
    path.write_text(content, encoding="utf-8")
    source = SourceFile(
        relative_path=path.name,
        content_hash="b" * 64,
        language="JSON",
        module=None,
        line_count=len(content.splitlines()),
        size_bytes=len(content.encode("utf-8")),
    )

    facts = ConfigurationAnalyzer().analyze(tmp_path, source)

    assert facts.warnings == []
    assert {item.key for item in facts.configurations} == {
        "endpoint",
        "label",
        "message",
    }


def test_pipeline_produces_verified_metadata_without_source_text(
    sample_repository: Path,
) -> None:
    manifest = RepositoryAnalysisPipeline(
        allowed_roots=[sample_repository],
        max_claims=50,
    ).run(sample_repository)
    serialized = json.dumps(manifest.to_dict())

    assert manifest.stage.value == "COMPLETED"
    assert manifest.metrics["planned_analysis_files"] == len(manifest.files)
    assert manifest.metrics["planned_analysis_tasks"] >= 1
    assert any(item.symbol == "Service" for item in manifest.symbols)
    assert any(item.key == "service.timeout" for item in manifest.configurations)
    assert any(item.name == "fastify" for item in manifest.dependencies)
    assert manifest.components
    assert any(item.responsibility for item in manifest.components)
    assert manifest.lifecycle_nodes
    assert manifest.dependency_usages
    assert any(
        item.knowledge_type == "LOGIC_FLOW" and item.processing_steps
        for item in manifest.knowledge_items
    )
    assert any(
        item.knowledge_type == "COMPONENT" and item.summary for item in manifest.knowledge_items
    )
    overview = next(
        item for item in manifest.knowledge_items if item.knowledge_type == "REPOSITORY_OVERVIEW"
    )
    assert overview.summary
    assert "## 기술 구성" in overview.detail
    assert overview.source_references
    assert manifest.claims
    assert all(
        item.validation_status == ValidationStatus.SOURCE_VERIFIED for item in manifest.claims
    )
    assert "never-store-this" not in serialized
    assert '"content":' not in serialized
    assert manifest.metrics["searchable_knowledge_items"] >= 1
    assert manifest.metrics["evaluation_cases"] == 15
    assert manifest.metrics["evaluation_scenarios"] == 15
    assert manifest.metrics["evaluation_evidence_integrity_passed"] == 15
    assert manifest.metrics["evaluation_answer_quality_executed"] == 0
    assert all(
        "retrieval_answerable" in item.grading_criteria for item in manifest.evaluation_cases
    )
    assert any(item.grading_criteria["retrieval_answerable"] for item in manifest.evaluation_cases)


def test_typescript_imports_are_connected_to_declared_dependencies(
    tmp_path: Path,
) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"react": "19.2.8"}}),
        encoding="utf-8",
    )
    (tmp_path / "page.tsx").write_text(
        'import React from "react";\n'
        'export function Page() { return React.createElement("div"); }\n',
        encoding="utf-8",
    )

    manifest = RepositoryAnalysisPipeline(allowed_roots=[tmp_path]).run(tmp_path)

    assert any(
        item.dependency_name == "react"
        and item.relative_path == "page.tsx"
        and item.provenance == "STATIC_CONFIRMED"
        for item in manifest.dependency_usages
    )
    assert not any(
        item.relation_type == "CALLS" and item.target_symbol in {"Page", "return"}
        for item in manifest.relations
    )


def test_static_diagnostic_relations_create_grounded_support_knowledge(
    tmp_path: Path,
) -> None:
    (tmp_path / "worker.py").write_text(
        """
import httpx

def run(endpoint):
    try:
        return httpx.get(endpoint)
    except TimeoutError:
        raise RuntimeError("request failed")
""".strip()
        + "\n",
        encoding="utf-8",
    )

    manifest = RepositoryAnalysisPipeline(allowed_roots=[tmp_path]).run(tmp_path)

    relation_types = {item.relation_type for item in manifest.relations}
    assert {"SENDS_HTTP", "HANDLES_EXCEPTION", "THROWS"} <= relation_types
    support_items = [
        item
        for item in manifest.knowledge_items
        if item.knowledge_type in {"ERROR_HANDLING", "RETRY_TIMEOUT"}
    ]
    assert len(support_items) == 2
    assert all(item.source_references for item in support_items)


def test_repository_lifecycle_connects_detected_phases(tmp_path: Path) -> None:
    (tmp_path / "discovery.py").write_text(
        "def discover():\n    return ['source.py']\n",
        encoding="utf-8",
    )
    (tmp_path / "repository.py").write_text(
        "def persist(items):\n    return len(items)\n",
        encoding="utf-8",
    )

    manifest = RepositoryAnalysisPipeline(allowed_roots=[tmp_path]).run(tmp_path)

    assert [item.phase for item in manifest.lifecycle_nodes] == [
        "DISCOVERY",
        "PERSISTENCE",
    ]
    assert any(
        item.source_key == "discovery"
        and item.target_key == "persistence"
        and item.provenance == "STATIC_INFERRED"
        for item in manifest.lifecycle_edges
    )


def test_derived_java_artifacts_are_components_instead_of_one_evidence_bucket() -> None:
    first = ".decompiled/indigo-core-1.4.0__abc/com/indigo/esb/Core.java"
    second = ".decompiled/indigo-jms-1.4.0__def/com/indigo/esb/Jms.java"

    assert _component_key(first) == ".decompiled/indigo-core-1.4.0__abc"
    assert _component_key(second) == ".decompiled/indigo-jms-1.4.0__def"
    assert _humanize(_component_key(first)) == "indigo core 1.4.0"


def test_repository_report_uses_only_claim_ids_that_pass_the_evidence_gate(
    sample_repository: Path,
) -> None:
    manifest = RepositoryAnalysisPipeline(allowed_roots=[sample_repository]).run(sample_repository)

    class ReportProvider:
        def synthesize_report(self, *, system, context):
            assert "claim_catalog" in context
            assert len(context["claim_catalog"]) <= 120
            assert len(context["repository"]["declared_dependencies"]) <= 60
            return ModelInvocation(
                payload={
                    "purpose": {
                        "text": "검증된 서비스 코드와 설정을 묶어 요청을 처리하는 저장소입니다.",
                        "claim_ids": ["C001"],
                    },
                    "capabilities": [
                        {
                            "text": "서비스 핸들러가 입력을 직렬화합니다.",
                            "claim_ids": ["C001"],
                        },
                        {
                            "text": "근거가 없는 기능입니다.",
                            "claim_ids": ["C999"],
                        },
                    ],
                    "technologies": [
                        {
                            "name": "Python",
                            "role": "서비스 처리 코드를 구현합니다.",
                            "claim_ids": ["C001"],
                        }
                    ],
                    "processing_flow": [
                        {
                            "phase": "INPUT",
                            "title": "입력",
                            "description": "서비스가 입력을 받습니다.",
                            "claim_ids": ["C001"],
                        },
                        {
                            "phase": "PROCESSING",
                            "title": "직렬화",
                            "description": "핸들러가 값을 직렬화합니다.",
                            "claim_ids": ["C002"],
                        },
                    ],
                    "operational_notes": [],
                    "unknowns": ["실행 환경의 실제 호출자는 정적 근거만으로 확정할 수 없습니다."],
                },
                model="test-model",
                prompt_version="test-report",
                latency_ms=1,
                prompt_tokens=10,
                completion_tokens=10,
            )

    report = synthesize_repository_report(manifest, ReportProvider())

    assert report.knowledge_type == "REPOSITORY_OVERVIEW"
    assert report.summary.startswith("검증된 서비스")
    assert "근거가 없는 기능" not in report.detail
    assert manifest.metrics["repository_report_evidence_rejected_statements"] == 1
    assert len(manifest.lifecycle_nodes) == 2
    assert manifest.lifecycle_edges[0].provenance == "EVIDENCE_SYNTHESIZED"
    assert manifest.metrics["repository_report_contract"] == "human-readable-v2"
    assert manifest.metrics["repository_report_quality_gate"] == "EVIDENCE_SYNTHESIZED"


def test_repository_report_fallback_is_fail_closed_about_purpose_and_sequence(
    sample_repository: Path,
) -> None:
    manifest = RepositoryAnalysisPipeline(allowed_roots=[sample_repository]).run(
        sample_repository
    )

    report = synthesize_repository_report(manifest, None)

    assert manifest.metrics["repository_report_contract"] == "human-readable-v2"
    assert manifest.metrics["repository_report_mode"] == "DETERMINISTIC_FALLBACK"
    assert manifest.metrics["repository_report_quality_gate"] == "LIMITED_FALLBACK"
    assert manifest.metrics["repository_report_model_failure"] == (
        "MODEL_PROVIDER_UNAVAILABLE"
    )
    assert "전체 목적은 모델 근거 종합이 완료되기 전까지 확정하지 않으며" in report.summary
    assert all(
        step.startswith(("추정 처리 영역", "검증된 역할"))
        for step in report.processing_steps
    )
    assert "저장소 분석 절차" not in report.detail
    assert "분석 결과를 데이터베이스에 저장" not in report.detail


def test_repository_report_claim_catalog_is_diverse_and_character_bounded(
    sample_repository: Path,
) -> None:
    manifest = RepositoryAnalysisPipeline(allowed_roots=[sample_repository]).run(
        sample_repository
    )
    evidence = manifest.claims[0].evidence
    manifest.claims = [
        Claim(
            claim=f"component-{index % 70} " + ("검증된 처리 설명 " * 35),
            claim_type="CODE_FACT",
            component=f"component-{index % 70}",
            evidence=evidence,
            confidence=Confidence.HIGH,
            validation_status=ValidationStatus.SOURCE_VERIFIED,
        )
        for index in range(240)
    ]

    catalog, _ = _claim_catalog(manifest)
    serialized = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))

    assert len(serialized) <= 22_000
    assert len({item["component"] for item in catalog}) >= 30
    assert all(len(item["evidence"]) == 1 for item in catalog)


def test_pipeline_snapshot_identity_is_deterministic(sample_repository: Path) -> None:
    pipeline = RepositoryAnalysisPipeline(allowed_roots=[sample_repository])
    first = pipeline.run(sample_repository)
    second = pipeline.run(sample_repository)

    assert first.project_id == second.project_id
    assert first.snapshot_id == second.snapshot_id
    assert first.source_hash == second.source_hash


def test_claim_validator_rejects_wrong_hash(sample_repository: Path) -> None:
    manifest = RepositoryAnalysisPipeline(allowed_roots=[sample_repository]).run(sample_repository)
    symbol = manifest.symbols[0]
    claim = Claim(
        claim="invalid evidence",
        claim_type="CODE_FACT",
        component=symbol.symbol,
        evidence=[
            EvidenceReference(
                file=symbol.relative_path,
                source_hash="0" * 64,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                symbol=symbol.symbol,
            )
        ],
        confidence=Confidence.HIGH,
    )

    validate_claim(
        claim,
        root=sample_repository,
        files=manifest.files,
        symbols=manifest.symbols,
        configurations=manifest.configurations,
        dependencies=manifest.dependencies,
    )

    assert claim.validation_status == ValidationStatus.PARTIALLY_VERIFIED
    assert claim.validation_errors == [f"source_hash_mismatch:{symbol.relative_path}"]


def test_claim_validator_checks_structured_configuration_evidence(
    sample_repository: Path,
) -> None:
    manifest = RepositoryAnalysisPipeline(allowed_roots=[sample_repository]).run(sample_repository)
    symbol = next(item for item in manifest.symbols if item.symbol == "Service")
    source = next(item for item in manifest.files if item.relative_path == symbol.relative_path)
    claim = Claim(
        claim="Service uses the declared timeout configuration.",
        claim_type="CONFIGURATION_FACT",
        component="Service",
        evidence=[
            EvidenceReference(
                file=symbol.relative_path,
                source_hash=source.content_hash,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                symbol=symbol.symbol,
            )
        ],
        related_configs=[
            {
                "key": "service.timeout",
                "file": "application.yaml",
                "used_by": "Service",
            }
        ],
        confidence=Confidence.HIGH,
    )

    validate_claim(
        claim,
        root=sample_repository,
        files=manifest.files,
        symbols=manifest.symbols,
        configurations=[
            ConfigurationReference(
                key="service.timeout",
                relative_path="application.yaml",
                declaration_line=2,
                referenced_by=("Service",),
            )
        ],
        dependencies=manifest.dependencies,
    )

    assert claim.validation_status == ValidationStatus.SOURCE_VERIFIED
    assert claim.validation_errors == []


def test_local_model_provider_refuses_cloud_endpoint() -> None:
    with pytest.raises(ValueError, match="must be local"):
        LocalModelProvider(base_url="https://api.example.com/v1", model="remote")


def test_local_model_provider_accepts_workstation_endpoints() -> None:
    provider = LocalModelProvider(
        base_url="http://host.docker.internal:11434/v1",
        model="local-model",
    )
    assert provider.base_url == "http://host.docker.internal:11434/v1"


def test_local_model_provider_reads_qwen36_mtp_environment(monkeypatch) -> None:
    monkeypatch.setenv("REPO_ANALYSIS_MODEL_ENABLED", "true")
    monkeypatch.setenv(
        "REPO_ANALYSIS_MODEL_BASE_URL",
        "http://host.docker.internal:18000/v1",
    )
    monkeypatch.setenv(
        "REPO_ANALYSIS_MODEL_NAME",
        "qwen3.6-27b-mtp-q4-k-m",
    )
    monkeypatch.setenv("REPO_ANALYSIS_MODEL_INCLUDE_SOURCE_EXCERPTS", "true")
    monkeypatch.setenv("REPO_ANALYSIS_MODEL_MAX_SOURCE_CHARS", "12000")

    provider = LocalModelProvider.from_environment()

    assert provider is not None
    assert provider.model == "qwen3.6-27b-mtp-q4-k-m"
    assert provider.include_source_excerpts is True
    assert provider.max_source_chars == 12000


def test_analysis_response_format_reduces_retry_claim_limit() -> None:
    initial = provider_module._response_format({"max_claims": 5})
    retry = provider_module._response_format({"max_claims": 2})

    assert initial["json_schema"]["schema"]["properties"]["claims"]["maxItems"] == 5
    assert retry["json_schema"]["schema"]["properties"]["claims"]["maxItems"] == 2
    assert provider_module._ANALYSIS_RESPONSE_SCHEMA["properties"]["claims"]["maxItems"] == 5
    report = provider_module._response_format({"task_type": "repository_report"})
    assert report["json_schema"]["name"] == "repository_report"
    assert report["json_schema"]["schema"]["required"] == [
        "purpose",
        "capabilities",
        "technologies",
        "processing_flow",
        "operational_notes",
        "unknowns",
    ]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ValidationStatus.SOURCE_VERIFIED, True),
        (ValidationStatus.PARTIALLY_VERIFIED, True),
        (ValidationStatus.TEST_VERIFIED, True),
        (ValidationStatus.RUNTIME_VERIFIED, True),
        (ValidationStatus.HUMAN_APPROVED, True),
        (ValidationStatus.ADDITIONAL_DATA_NEEDED, True),
        (ValidationStatus.CONTRADICTION, False),
        (ValidationStatus.ADDITIONAL_ANALYSIS_REQUIRED, False),
        (ValidationStatus.RUNTIME_EVIDENCE_REQUIRED, False),
        (ValidationStatus.MANUAL_REVIEW_REQUIRED, False),
        (ValidationStatus.UNRESOLVED, False),
        (ValidationStatus.REJECTED, False),
        (ValidationStatus.STALE, False),
    ],
)
def test_knowledge_searchability_follows_validation_policy(
    status: ValidationStatus,
    expected: bool,
) -> None:
    item = KnowledgeItem(
        knowledge_type="COMPONENT_FLOW",
        title="title",
        summary="summary",
        detail="detail",
        processing_steps=[],
        components=[],
        configurations=[],
        dependencies=[],
        source_references=[],
        validation_status=status,
        confidence=Confidence.LOW,
        unknowns=[],
        analysis_version="test",
    )

    assert item.searchable is expected


def test_local_model_provider_disables_thinking_for_structured_output(
    monkeypatch,
) -> None:
    captured = {}

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [{"message": {"content": '{"claims":[]}'}}],
                "usage": {},
            }

    def fake_post(url, *, json, timeout):
        captured.update({"url": url, "json": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr(provider_module.httpx, "post", fake_post)
    provider = LocalModelProvider(
        base_url="http://host.docker.internal:18000/v1",
        model="qwen3.6-27b-mtp-q4-k-m",
    )

    invocation = provider.analyze(system="Return JSON.", context={"evidence": []})

    assert invocation.payload == {"claims": []}
    assert captured["json"]["chat_template_kwargs"] == {"enable_thinking": False}
    response_format = captured["json"]["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["name"] == "repository_analysis"
    assert response_format["json_schema"]["schema"]["required"] == [
        "claims",
        "contradictions",
        "missing_knowledge",
    ]

    provider.analyze(
        system="Return a support answer.",
        context={"question_type": "ENTRYPOINT", "knowledge": []},
    )
    response_format = captured["json"]["response_format"]
    assert response_format["json_schema"]["name"] == "repository_support_answer"
    assert "confidence" in response_format["json_schema"]["schema"]["required"]


def test_model_analysis_uses_bounded_evidence_excerpts(
    sample_repository: Path,
) -> None:
    class RecordingProvider:
        include_source_excerpts = True
        max_source_chars = 2000

        def __init__(self) -> None:
            self.analysis_contexts = []
            self.evaluation_contexts = []

        def analyze(self, *, system, context):
            if "question_type" in context:
                self.evaluation_contexts.append(context)
                reference = context["knowledge"][0]["source_references"][0]
                return ModelInvocation(
                    payload={
                        "confirmed_facts": ["제공된 근거에서 확인되는 사실입니다."],
                        "hypotheses": [],
                        "counter_evidence": [],
                        "source_references": [reference],
                        "configurations": [],
                        "additional_data": ["운영 로그"],
                        "next_steps": ["인용된 코드와 운영 로그를 비교합니다."],
                        "confidence": "MEDIUM",
                    },
                    model="qwen3.6-27b-mtp-q4-k-m",
                    prompt_version="repo-analysis-v2-evidence-excerpts",
                    latency_ms=10,
                    prompt_tokens=100,
                    completion_tokens=20,
                )
            self.analysis_contexts.append(context)
            excerpt = context["source_excerpts"][0]
            return ModelInvocation(
                payload={
                    "claims": [
                        {
                            "claim": "The supplied symbol is present.",
                            "claim_type": "DESIGN_INFERENCE",
                            "component": "Service",
                            "evidence": [
                                {
                                    "file": excerpt["file"],
                                    "source_hash": excerpt["source_hash"],
                                    "start_line": excerpt["start_line"],
                                    "end_line": excerpt["end_line"],
                                    "symbol": "Service",
                                }
                            ],
                            "confidence": "MEDIUM",
                        }
                    ],
                    "contradictions": [],
                    "missing_knowledge": [],
                },
                model="qwen3.6-27b-mtp-q4-k-m",
                prompt_version="repo-analysis-v2-evidence-excerpts",
                latency_ms=10,
                prompt_tokens=100,
                completion_tokens=20,
            )

    provider = RecordingProvider()
    manifest = RepositoryAnalysisPipeline(
        allowed_roots=[sample_repository],
        provider=provider,
    ).run(sample_repository)

    assert provider.analysis_contexts
    assert provider.analysis_contexts[0]["source_excerpts"]
    assert all(
        sum(len(item["text"]) for item in context["source_excerpts"]) <= provider.max_source_chars
        for context in provider.analysis_contexts
    )
    assert len(provider.evaluation_contexts) == 15
    assert manifest.metrics["llm_claims_accepted"] >= 1
    assert manifest.metrics["llm_requests"] == manifest.metrics["llm_tasks"]
    assert manifest.metrics["llm_tasks"] >= 2
    assert all(
        item["started_at"] is not None and item["finished_at"] is not None
        for item in manifest.metrics["llm_task_outcomes"]
    )
    assert manifest.metrics["operator_intervention_required"] is False
    assert manifest.metrics["codex_intervention_policy"] == "RECORD_FAILURE_AND_CONTINUE"
    assert manifest.metrics["codex_source_substitution_tasks"] == 0
    assert manifest.metrics["evaluation_answer_quality_executed"] == 15
    assert manifest.metrics["evaluation_answer_quality_passed"] == 15
    assert all(
        "never-store-this" not in item["text"]
        for context in provider.analysis_contexts
        for item in context["source_excerpts"]
    )


def test_repository_analysis_cli_serializes_restored_task_datetimes(
    monkeypatch,
    capsys,
) -> None:
    restored_at = datetime(2026, 7, 29, tzinfo=timezone.utc)

    class Manifest:
        project_id = uuid.uuid4()
        snapshot_id = uuid.uuid4()
        source_hash = "a" * 64
        metrics = {"llm_task_outcomes": [{"started_at": restored_at}]}
        warnings = []

        class Stage:
            value = "COMPLETED"

        stage = Stage()

    class Pipeline:
        def __init__(self, **kwargs):
            pass

        def run(self, source_root):
            return Manifest()

    monkeypatch.setattr(
        repository_analysis_main,
        "RepositoryAnalysisPipeline",
        Pipeline,
    )
    monkeypatch.setattr(
        "sys.argv",
        ["repository-analysis", "/tmp/source", "--allowed-root", "/tmp/source"],
    )

    assert repository_analysis_main.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["metrics"]["llm_task_outcomes"][0]["started_at"] == str(restored_at)


def test_model_analysis_resumes_from_completed_task_checkpoint(
    sample_repository: Path,
) -> None:
    class MemoryCheckpointStore:
        def __init__(self) -> None:
            self.entries = {}
            self.fail_after_first_save = True
            self.completed = False

        def begin(self, manifest, *, fingerprint, planned_task_count):
            return uuid.UUID("90fead62-0d50-4a2a-9da1-bc1077f36864")

        def restore(self, checkpoint, tasks):
            return dict(self.entries)

        def save_task(self, checkpoint, outcome, claims):
            self.entries[str(outcome["task_id"])] = (
                dict(outcome),
                list(claims),
            )
            if self.fail_after_first_save:
                self.fail_after_first_save = False
                raise RuntimeError("simulated interruption after durable save")

        def mark_tasks_complete(self, checkpoint, metrics):
            self.completed = True

    class CheckpointProvider:
        include_source_excerpts = True
        max_source_chars = 2_000
        max_output = 512
        model = "qwen3.6-27b-mtp-q4-k-m"
        model_quantization = "Q4_K_M"
        prompt_version = "repo-analysis-v2-evidence-excerpts"

        def __init__(self) -> None:
            self.analysis_calls = 0

        def analyze(self, *, system, context):
            if "question_type" in context:
                reference = context["knowledge"][0]["source_references"][0]
                return ModelInvocation(
                    payload={
                        "confirmed_facts": ["The evidence is present."],
                        "hypotheses": [],
                        "counter_evidence": [],
                        "source_references": [reference],
                        "configurations": [],
                        "additional_data": [],
                        "next_steps": ["Review the cited evidence."],
                        "confidence": "HIGH",
                    },
                    model=self.model,
                    prompt_version=self.prompt_version,
                    latency_ms=1,
                    prompt_tokens=1,
                    completion_tokens=1,
                )
            self.analysis_calls += 1
            excerpt = context["source_excerpts"][0]
            return ModelInvocation(
                payload={
                    "claims": [
                        {
                            "claim": (f"Checkpointed analysis for {context['analysis_unit']}."),
                            "claim_type": "CODE_FACT",
                            "component": context["analysis_unit"],
                            "evidence": [
                                {
                                    "file": excerpt["file"],
                                    "source_hash": excerpt["source_hash"],
                                    "start_line": excerpt["start_line"],
                                    "end_line": excerpt["end_line"],
                                    "symbol": None,
                                }
                            ],
                            "related_configs": [],
                            "assumptions": [],
                            "unknowns": [],
                            "counter_evidence": [],
                            "confidence": "HIGH",
                        }
                    ],
                    "contradictions": [],
                    "missing_knowledge": [],
                },
                model=self.model,
                prompt_version=self.prompt_version,
                latency_ms=1,
                prompt_tokens=1,
                completion_tokens=1,
            )

    checkpoint_store = MemoryCheckpointStore()
    first_provider = CheckpointProvider()
    with pytest.raises(RuntimeError, match="simulated interruption"):
        RepositoryAnalysisPipeline(
            allowed_roots=[sample_repository],
            provider=first_provider,
            checkpoint_store=checkpoint_store,
        ).run(sample_repository)

    assert first_provider.analysis_calls == 1
    assert len(checkpoint_store.entries) == 1

    second_provider = CheckpointProvider()
    manifest = RepositoryAnalysisPipeline(
        allowed_roots=[sample_repository],
        provider=second_provider,
        checkpoint_store=checkpoint_store,
    ).run(sample_repository)

    assert checkpoint_store.completed is True
    assert manifest.metrics["checkpoint_restored_tasks"] == 1
    assert second_provider.analysis_calls == manifest.metrics["planned_analysis_tasks"] - 1
    assert manifest.metrics["checkpoint_saved_tasks"] == (
        manifest.metrics["planned_analysis_tasks"] - 1
    )
    assert manifest.metrics["llm_tasks_executed"] == (manifest.metrics["planned_analysis_tasks"])


def test_repository_embedding_text_contains_support_context() -> None:
    value = embedding_text(
        {
            "title": "외부 요청과 재시도",
            "knowledge_type": "RETRY_TIMEOUT",
            "summary": "시간 초과 처리 위치",
            "detail": "재시도 종료 조건을 확인합니다.",
            "processing_steps": ["요청", "재시도", "종료"],
            "components": ["worker"],
            "configurations": ["request.timeout"],
            "dependencies": ["httpx"],
            "validation_status": "SOURCE_VERIFIED",
            "unknowns": ["실행 시점 응답"],
        }
    )

    assert "request.timeout" in value
    assert "재시도 종료 조건" in value
    assert "SOURCE_VERIFIED" in value


def test_post_persistence_retrieval_rank_requires_exact_evidence() -> None:
    expected = [
        {
            "file": "src/service.py",
            "source_hash": "a" * 64,
            "start_line": 3,
            "end_line": 5,
            "symbol": "Service",
        }
    ]
    retrieved = [
        {"source_references": []},
        {"source_references": [expected[0]]},
    ]

    assert _expected_rank(expected, retrieved) == 2
    assert _expected_rank(expected, [{"source_references": []}]) is None


def test_pipeline_indexes_prefixed_decompiled_jar_evidence(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "agent.properties").write_text(
        "agent.enabled=true\n",
        encoding="utf-8",
    )
    evidence = tmp_path / "decompiled"
    jar_source = evidence / "internal-agent" / "com" / "indigo" / "Agent.java"
    jar_source.parent.mkdir(parents=True)
    jar_source.write_text(
        "package com.indigo; public class Agent { public void start() {} }\n",
        encoding="utf-8",
    )

    manifest = RepositoryAnalysisPipeline(
        allowed_roots=[repository],
        evidence_roots={".decompiled": evidence},
    ).run(repository)

    evidence_path = ".decompiled/internal-agent/com/indigo/Agent.java"
    assert any(item.relative_path == evidence_path for item in manifest.files)
    assert any(
        item.relative_path == evidence_path and item.symbol == "Agent" for item in manifest.symbols
    )
    assert any(
        reference.file == evidence_path for claim in manifest.claims for reference in claim.evidence
    )
    assert all(
        claim.validation_status != ValidationStatus.REJECTED
        for claim in manifest.claims
        if any(reference.file == evidence_path for reference in claim.evidence)
    )


def test_pipeline_marks_hash_verified_derived_replacement_as_resolved(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    oversized = repository / "bundle.js"
    oversized.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    evidence = tmp_path / "normalized"
    evidence.mkdir()
    derived = b"function recovered() {}\n"
    (evidence / "source-part-0001.js").write_bytes(derived)
    (evidence / "evidence-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "TEXT_CHUNKS",
                "source_label": "bundle.js",
                "source_sha256": hashlib.sha256(oversized.read_bytes()).hexdigest(),
                "chunks": [
                    {
                        "file": "source-part-0001.js",
                        "content_sha256": hashlib.sha256(derived).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = RepositoryAnalysisPipeline(
        allowed_roots=[repository],
        evidence_roots={".normalized": evidence},
    ).run(repository)

    assert manifest.metrics["skipped_file_details"] == [
        {"path": "bundle.js", "reason": "oversized"}
    ]
    assert manifest.metrics["resolved_skipped_files"] == [
        {
            "source_path": "bundle.js",
            "evidence_prefix": ".normalized",
            "source_sha256": hashlib.sha256(oversized.read_bytes()).hexdigest(),
            "kind": "TEXT_CHUNKS",
        }
    ]
    assert manifest.metrics["unresolved_skipped_file_count"] == 0


def test_model_analysis_batches_every_decompiled_file(tmp_path: Path) -> None:
    class RecordingProvider:
        include_source_excerpts = False
        max_source_chars = 0

        def __init__(self) -> None:
            self.contexts = []

        def analyze(self, *, system, context):
            self.contexts.append(context)
            return ModelInvocation(
                payload={
                    "claims": [],
                    "contradictions": [],
                    "missing_knowledge": [],
                },
                model="qwen3.6-27b-mtp-q4-k-m",
                prompt_version="repo-analysis-v2-evidence-excerpts",
                latency_ms=1,
                prompt_tokens=1,
                completion_tokens=1,
            )

    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "app.properties").write_text("enabled=true\n", encoding="utf-8")
    evidence = tmp_path / "decompiled"
    for index in range(61):
        source = evidence / "internal.jar" / f"Class{index:02d}.java"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            f"public class Class{index:02d} {{}}\n",
            encoding="utf-8",
        )

    provider = RecordingProvider()
    manifest = RepositoryAnalysisPipeline(
        allowed_roots=[repository],
        evidence_roots={".decompiled": evidence},
        provider=provider,
    ).run(repository)

    jar_contexts = [
        context
        for context in provider.contexts
        if context.get("task_id")
        and any(
            item["path"].startswith(".decompiled/internal.jar/")
            for item in context.get("files", [])
        )
    ]
    analyzed_paths = {item["path"] for context in jar_contexts for item in context["files"]}
    assert len(jar_contexts) == 13
    assert all(len(context["files"]) <= 5 for context in jar_contexts)
    assert len(analyzed_paths) == 61
    assert manifest.metrics["llm_requests"] == manifest.metrics["llm_tasks"]


def test_model_analysis_records_reduced_retry_and_evidence_request(
    sample_repository: Path,
) -> None:
    class RetryProvider:
        include_source_excerpts = True
        max_source_chars = 30_000

        def __init__(self) -> None:
            self.attempts: dict[str, int] = {}

        def analyze(self, *, system, context):
            task_id = context["task_id"]
            attempt = self.attempts.get(task_id, 0) + 1
            self.attempts[task_id] = attempt
            if attempt == 1:
                raise TimeoutError("response body must not be persisted")
            return ModelInvocation(
                payload={
                    "claims": [],
                    "contradictions": [],
                    "missing_knowledge": ["운영 시점의 요청 로그"],
                },
                model="qwen3.6-27b-mtp-q4-k-m",
                prompt_version="repo-analysis-v2-evidence-excerpts",
                latency_ms=1,
                prompt_tokens=1,
                completion_tokens=1,
            )

    manifest = RepositoryAnalysisPipeline(
        allowed_roots=[sample_repository],
        provider=RetryProvider(),
    ).run(sample_repository)

    outcomes = manifest.metrics["llm_task_outcomes"]
    assert outcomes
    assert manifest.metrics["llm_unresolved_tasks"] == len(outcomes)
    assert manifest.metrics["llm_requests"] == len(outcomes) * 2
    assert manifest.metrics["operator_intervention_required"] is False
    assert manifest.metrics["codex_intervention_policy"] == "RECORD_FAILURE_AND_CONTINUE"
    assert (
        manifest.metrics["analysis_failures_recorded"] == manifest.metrics["llm_unresolved_tasks"]
    )
    assert manifest.metrics["codex_source_substitution_tasks"] == 0
    assert all(item["attempts"] == 2 for item in outcomes)
    assert all(
        item["failure_history"]
        == [
            {
                "attempt": 1,
                "failure_code": "TimeoutError",
                "requested_source_char_budget": 4_500,
            }
        ]
        for item in outcomes
    )
    assert all(
        item["retry_history"]
        == [
            {
                "attempt": 2,
                "strategy": "REDUCED_SOURCE_BUDGET",
                "requested_source_char_budget": 2_250,
                "trigger_failure_code": "TimeoutError",
            }
        ]
        for item in outcomes
    )
    assert all(
        item["additional_evidence_requests"] == ["운영 시점의 요청 로그"]
        and item["status"] == "ADDITIONAL_ANALYSIS_REQUIRED"
        and item["failure_code"] == "ADDITIONAL_EVIDENCE_REQUIRED"
        and item["codex_intervention_policy"] == "RECORD_FAILURE_AND_CONTINUE"
        and item["codex_source_substitution"] is False
        and item["failure_recorded_and_continued"] is True
        for item in outcomes
    )
    assert "response body must not be persisted" not in str(outcomes)


def test_model_analysis_records_every_task_when_claim_limit_is_reached(
    sample_repository: Path,
) -> None:
    class LimitProvider:
        include_source_excerpts = False
        max_source_chars = 0

        def analyze(self, *, system, context):
            if "question_type" in context:
                reference = context["knowledge"][0]["source_references"][0]
                return ModelInvocation(
                    payload={
                        "confirmed_facts": ["근거가 확인되었습니다."],
                        "hypotheses": [],
                        "counter_evidence": [],
                        "source_references": [reference],
                        "configurations": [],
                        "additional_data": [],
                        "next_steps": ["인용된 근거를 확인합니다."],
                        "confidence": "HIGH",
                    },
                    model="qwen3.6-27b-mtp-q4-k-m",
                    prompt_version="repo-analysis-v2-evidence-excerpts",
                    latency_ms=1,
                    prompt_tokens=1,
                    completion_tokens=1,
                )
            file = context["files"][0]
            return ModelInvocation(
                payload={
                    "claims": [
                        {
                            "claim": "첫 번째 배치에서 검증 가능한 사실입니다.",
                            "claim_type": "CODE_FACT",
                            "component": context["analysis_unit"],
                            "evidence": [
                                {
                                    "file": file["path"],
                                    "source_hash": file["hash"],
                                    "start_line": 1,
                                    "end_line": 1,
                                    "symbol": None,
                                }
                            ],
                            "related_configs": [],
                            "assumptions": [],
                            "unknowns": [],
                            "counter_evidence": [],
                            "confidence": "HIGH",
                        }
                    ],
                    "contradictions": [],
                    "missing_knowledge": [],
                },
                model="qwen3.6-27b-mtp-q4-k-m",
                prompt_version="repo-analysis-v2-evidence-excerpts",
                latency_ms=1,
                prompt_tokens=1,
                completion_tokens=1,
            )

    manifest = RepositoryAnalysisPipeline(
        allowed_roots=[sample_repository],
        provider=LimitProvider(),
        max_claims=1,
    ).run(sample_repository)

    outcomes = manifest.metrics["llm_task_outcomes"]
    assert manifest.metrics["llm_tasks"] > 1
    assert len(outcomes) == manifest.metrics["llm_tasks"]
    assert {path for item in outcomes for path in item["source_files"]} == {
        item.relative_path for item in manifest.files
    }
    assert manifest.metrics["llm_tasks_executed"] == 1
    assert manifest.metrics["llm_tasks_skipped_claim_limit"] == manifest.metrics["llm_tasks"] - 1
    assert outcomes[0]["status"] == "SOURCE_EXTRACTED"
    assert all(
        item["status"] == "ADDITIONAL_ANALYSIS_REQUIRED"
        and item["failure_code"] == "CLAIM_LIMIT_REACHED"
        and item["attempts"] == 0
        and item["codex_intervention_policy"] == "RECORD_FAILURE_AND_CONTINUE"
        and item["codex_source_substitution"] is False
        and item["failure_recorded_and_continued"] is True
        for item in outcomes[1:]
    )
