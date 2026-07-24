import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from lkp.models import (
    ActivityEvent,
    EvidenceRecord,
    GeneratedPage,
    IngestJob,
    KnowledgeCandidate,
    KnowledgeCase,
    KnowledgeCaseRelation,
    KnowledgeCaseRevision,
    KnowledgeOccurrence,
    SourceRoot,
)
from lkp.settings import Settings
from lkp_indexer.activity_knowledge import finalize_pending_stops, finalize_stop
from lkp_indexer.activity_retention import roll_up_activity_details
from lkp_indexer.case_pages import materialize_case
from lkp_indexer.generation import CuratedKnowledgeArticle, EvidenceBoundParagraph
from lkp_indexer.hook_collector import collect_file, envelope_to_activity
from lkp_indexer.hook_spool import spool
from lkp_indexer.knowledge import (
    create_candidate,
    invalidate_misclassified_execution_evidence,
    publish_candidate,
)
from lkp_indexer.knowledge_curator import curate_candidate
from lkp_indexer.knowledge_quality import review_low_quality_auto_cases
from lkp_indexer.paths import idempotency_key
from lkp_indexer.queue import enqueue
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url():
    value = os.getenv("LKP_TEST_DATABASE_URL")
    if not value:
        pytest.skip("LKP_TEST_DATABASE_URL is not set")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", value)
    command.upgrade(config, "head")
    return value


def evidence(*items: tuple[str, int | None]) -> list[dict]:
    return [
        {
            "evidence_type": evidence_type,
            "claim": evidence_type,
            "exit_code": exit_code,
            "verified": True,
        }
        for evidence_type, exit_code in items
    ]


class FakeEvidenceEditor:
    provider = "test-local"
    model = "test-e4b"

    def model_digest(self) -> str:
        return "sha256:test-e4b"

    def curate(self, _payload, *, language, prompt_version):
        assert language == "ko"
        assert prompt_version == "evidence-blog-v9"
        section = [
            EvidenceBoundParagraph(
                text=(
                    "검증된 코드 변경과 독립 실행 테스트를 함께 확인했다. 작업 보고의 "
                    "표현은 근거로 사용하지 않았으며 관측 가능한 구현과 결과만 정리했다."
                ),
                evidence_ids=["E1", "E2"],
            )
        ]
        return (
            CuratedKnowledgeArticle(
                decision="publish",
                category="implementation",
                title="근거를 인용하는 자동 지식 편집 절차",
                standfirst="실행 근거가 있는 구현만 장문 사례로 승격하는 방식이다.",
                standfirst_evidence_ids=["E1", "E2"],
                context=section,
                problem=section,
                cause_or_decision=section,
                implementation=section,
                verification=section,
                limitations=[
                    EvidenceBoundParagraph(
                        text="장시간 운영 부하는 이 사례의 검증 범위에 포함되지 않았다.",
                        evidence_ids=["E1", "E2"],
                    )
                ],
                unsupported_inferences=[],
                decision_reason="독립된 두 근거가 재사용 가능한 구현을 지지한다.",
            ),
            self.model_digest(),
        )


def candidate(session: Session, **overrides):
    values = {
        "category": "error_resolution",
        "title": "pytest fixture failure",
        "problem": "fixture test failed",
        "symptom": "assertion failed in test fixture",
        "root_cause": "fixture omitted required field",
        "solution": "add the required field",
        "reported_result": "fixed",
        "verified_result": "pytest exit 0",
        "evidence": evidence(("command_failure", 1), ("code_change", None), ("test_pass", 0)),
    }
    values.update(overrides)
    return create_candidate(session, **values)


def test_local_editor_publishes_only_after_deterministic_validation(
    database_url: str, tmp_path: Path
):
    engine = create_engine(database_url)
    settings = Settings(
        database_url=database_url,
        vault_dir=tmp_path / "vault",
        knowledge_content_language="ko",
        knowledge_curation_auto_publish=True,
        knowledge_curation_min_article_chars=300,
    )
    with Session(engine) as session:
        row = create_candidate(
            session,
            category="implementation",
            title="자동 지식 편집 구현",
            problem="실행 보고가 지식으로 바로 게시될 수 있었다.",
            symptom="검증되지 않은 짧은 요약이 후보로 생성됐다.",
            root_cause="활동 근거와 게시 가능한 설명을 분리해야 했다.",
            solution="근거 인용과 결정론적 검사를 통과한 본문만 게시한다.",
            reported_result="완벽하게 구현됐다.",
            verified_result="테스트 성공",
            evidence=evidence(("code_change", None), ("test_pass", 0)),
            metadata={
                "auto_generated": True,
                "structured_knowledge": True,
                "project": "local-knowledge-portal",
                "tags": ["platform:wsl2"],
            },
        )
        outcome, case_id = curate_candidate(
            session,
            row,
            FakeEvidenceEditor(),
            settings,
            "sha256:test-e4b",
        )
        assert outcome == "CREATED_CANONICAL"
        assert case_id is not None
        assert row.status == "published"
        assert row.metadata_json["curation"]["state"] == "published"
        case = session.get(KnowledgeCase, uuid.UUID(case_id))
        assert case is not None
        page = next((tmp_path / "vault" / "_generated" / "Knowledge-Cases").glob("*.md"))
        content = page.read_text(encoding="utf-8")
        assert "## 상황과 맥락" in content
        assert "완벽하게 구현됐다" not in content
        assert "## 검증 근거" in content
        assert 'project: "local-knowledge-portal"' in content
        assert "situation:implementation" in content
        overview = (
            tmp_path
            / "vault"
            / "_generated"
            / "Projects"
            / "local-knowledge-portal"
            / "overview.md"
        )
        assert overview.exists()
        assert "## 최근 검증 사례" in overview.read_text(encoding="utf-8")
        session.rollback()


def test_collector_separates_reported_and_verified(database_url: str, tmp_path: Path):
    engine = create_engine(database_url)
    session_id = f"s-collector-{uuid.uuid4()}"
    instruction_raw = (
        '{"session_id":"' + session_id + '","turn_id":"t-collector",'
        '"hook_event_name":"UserPromptSubmit","cwd":"C:\\\\Dev\\\\Repos\\\\sample",'
        '"prompt":"테스트 실패 원인을 수정하고 다시 검증해줘"}'
    ).encode()
    stop_raw = (
        '{"session_id":"' + session_id + '","turn_id":"t-collector",'
        '"hook_event_name":"Stop","cwd":"C:\\\\Dev\\\\Repos\\\\sample",'
        '"last_assistant_message":"all tests pass"}'
    ).encode()
    instruction_path = spool(instruction_raw, tmp_path / "spool", tmp_path / "fallback")
    stop_path = spool(stop_raw, tmp_path / "spool", tmp_path / "fallback")
    with Session(engine) as session:
        assert collect_file(session, instruction_path)
        session.commit()
        assert collect_file(session, stop_path)
        session.commit()
        row = session.scalar(
            select(ActivityEvent).where(
                ActivityEvent.session_id == session_id,
                ActivityEvent.event_type == "Stop",
            )
        )
        assert row is not None
        assert row.reported_result == "all tests pass"
        assert row.verified_result is None
        assert row.verification_status == "UNVERIFIED"
        assert not collect_file(session, stop_path)


def test_evidence_gate_dedup_and_relationships(database_url: str, tmp_path: Path):
    engine = create_engine(database_url)
    with Session(engine) as session:
        first = candidate(session)
        first_case, outcome = publish_candidate(session, first)
        assert outcome == "CREATED_CANONICAL"
        assert first_case is not None

        repeated = candidate(session, title="same issue happened again")
        merged, outcome = publish_candidate(session, repeated)
        assert outcome == "MERGED_OCCURRENCE"
        assert merged is not None and merged.id == first_case.id
        assert merged.occurrence_count == 2
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeOccurrence)
                .where(KnowledgeOccurrence.case_id == first_case.id)
            )
            == 2
        )

        revised_candidate = candidate(
            session,
            title="pytest fixture failure corrected guidance",
            solution="add the required field and validate the fixture schema",
            metadata={"supersedes_case_id": str(first_case.id)},
        )
        revised, outcome = publish_candidate(session, revised_candidate)
        assert outcome == "REVISED_CANONICAL"
        assert revised is not None and revised.id == first_case.id
        assert revised.occurrence_count == 3
        assert revised.solution.endswith("validate the fixture schema")
        assert (
            session.scalar(
                select(func.count())
                .select_from(KnowledgeCaseRevision)
                .where(KnowledgeCaseRevision.case_id == first_case.id)
            )
            == 3
        )
        vault_dir = (tmp_path / "vault").resolve()
        vault_root = SourceRoot(
            name=f"test-vault-{uuid.uuid4()}",
            canonical_path=str(vault_dir),
            source_type="obsidian",
            data_scope="validation",
            read_only=False,
            enabled=True,
            include_patterns=[],
            exclude_patterns=[],
        )
        session.add(vault_root)
        session.flush()
        materialized = materialize_case(
            session,
            revised,
            vault_dir=vault_dir,
            pipeline_version="integration-v1",
        )
        materialized_content = materialized.read_text(encoding="utf-8")
        assert "validate the fixture schema" in materialized_content
        assert "case_revision: 3" in materialized_content
        materialized_mtime = materialized.stat().st_mtime_ns
        assert (
            materialize_case(
                session,
                revised,
                vault_dir=vault_dir,
                pipeline_version="integration-v1",
            )
            == materialized
        )
        assert materialized.stat().st_mtime_ns == materialized_mtime
        assert session.scalar(select(func.count()).select_from(GeneratedPage)) == 1
        info = materialized.stat()
        assert (
            enqueue(
                session,
                key=idempotency_key(
                    str(vault_root.id),
                    str(materialized),
                    info.st_size,
                    info.st_mtime_ns,
                ),
                source_root_id=vault_root.id,
                canonical_path=str(materialized),
                job_type="watch_index",
            )
            is None
        )
        assert session.scalar(select(func.count()).select_from(IngestJob)) == 1

        other_cause = candidate(
            session,
            title="same symptom from database",
            root_cause="database transaction was rolled back",
            solution="commit transaction before assertion",
        )
        separate, outcome = publish_candidate(session, other_cause)
        assert outcome == "CREATED_CANONICAL"
        assert separate is not None and separate.id != first_case.id
        relation = session.scalar(
            select(KnowledgeCaseRelation).where(
                KnowledgeCaseRelation.source_case_id == first_case.id,
                KnowledgeCaseRelation.target_case_id == separate.id,
            )
        )
        assert relation is not None
        assert relation.relation_type == "same_symptom_different_cause"

        uncertain = candidate(
            session,
            title="possible duplicate with a different symptom label",
            symptom="intermittent fixture validation error",
            solution="add the required field and rerun tests",
        )
        no_case, outcome = publish_candidate(session, uncertain)
        assert no_case is None
        assert outcome == "NEEDS_REVIEW"
        assert uncertain.status == "needs_review"

        unmeasured = candidate(
            session,
            category="performance",
            title="claimed speedup",
            problem="slow request",
            symptom="latency",
            root_cause="unknown load",
            solution="cache it",
            evidence=evidence(("code_change", None), ("test_pass", 0)),
        )
        no_case, outcome = publish_candidate(session, unmeasured)
        assert no_case is None
        assert outcome == "NEEDS_EVIDENCE"
        assert unmeasured.status != "published"

        measured = candidate(
            session,
            category="performance",
            title="measured queue improvement",
            problem="queue latency under load",
            symptom="p95 queue latency high",
            root_cause="lock contention under 20 workers",
            solution="shorten lease transaction",
            evidence=evidence(
                ("performance_before", None),
                ("performance_after", None),
                ("load_cause", None),
            ),
        )
        performance_case, outcome = publish_candidate(session, measured)
        assert performance_case is not None
        assert outcome == "CREATED_CANONICAL"

        session.flush()
        assert session.scalar(select(func.count()).select_from(KnowledgeCase)) >= 3
        assert (
            session.scalar(
                select(func.count())
                .select_from(EvidenceRecord)
                .where(EvidenceRecord.candidate_id == unmeasured.id)
            )
            == 2
        )
        session.rollback()


def test_global_activity_turns_become_evidence_gated_cases(database_url: str, tmp_path: Path):
    engine = create_engine(database_url)
    session_id = f"global-session-{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        change = ActivityEvent(
            event_key=f"change-{uuid.uuid4()}",
            session_id=session_id,
            turn_id="turn-complete",
            event_type="PostToolUse",
            occurred_at=now,
            project_key="thread-folder",
            cwd=r"C:\Users\kutae\Documents\Codex\thread-folder",
            tool_name="apply_patch",
            command=(
                "*** Begin Patch\n"
                "*** Update File: C:\\Dev\\Repos\\sample-project\\tests\\test_worker.py\n"
                "+pytest -q tests/test_worker.py\n"
                "*** End Patch"
            ),
            exit_code=0,
            changed_files=[
                r"C:\Dev\Repos\sample-project\services\worker.py",
                r"C:\Dev\Repos\sample-project\tests\test_worker.py",
            ],
            document_version_ids=[],
            verified_result="observed exit_code=0",
            verification_status="VERIFIED",
            metadata_json={},
        )
        validation = ActivityEvent(
            event_key=f"validation-{uuid.uuid4()}",
            session_id=session_id,
            turn_id="turn-complete",
            event_type="PostToolUse",
            occurred_at=now + timedelta(seconds=1),
            project_key="thread-folder",
            cwd=r"C:\Users\kutae\Documents\Codex\thread-folder",
            tool_name="Bash",
            command="pytest -q tests/test_worker.py",
            exit_code=0,
            changed_files=[],
            document_version_ids=[],
            verified_result="observed exit_code=0",
            verification_status="VERIFIED",
            metadata_json={},
        )
        complete = ActivityEvent(
            event_key=f"stop-{uuid.uuid4()}",
            session_id=session_id,
            turn_id="turn-complete",
            event_type="Stop",
            occurred_at=now + timedelta(seconds=2),
            project_key="thread-folder",
            cwd=r"C:\Users\kutae\Documents\Codex\thread-folder",
            instruction="worker 재시작 안전장치를 구현하고 회귀 테스트해",
            changed_files=[],
            document_version_ids=[],
            reported_result=(
                "구현 완료\n"
                "목표: worker 재시작 시 중복 처리를 방지합니다.\n"
                "구현 방식: lease 만료와 idempotency key를 함께 검사합니다.\n"
                "검증: 회귀 테스트 PASS"
            ),
            verified_result="apply_patch exit=0; Bash exit=0",
            verification_status="VERIFIED",
            metadata_json={},
        )
        incomplete = ActivityEvent(
            event_key=f"stop-{uuid.uuid4()}",
            session_id=session_id,
            turn_id="turn-incomplete",
            event_type="Stop",
            occurred_at=now + timedelta(seconds=3),
            project_key="thread-folder",
            cwd=r"C:\Users\kutae\Documents\Codex\thread-folder",
            instruction="성능 비교를 계속 진행해",
            changed_files=[],
            document_version_ids=[],
            reported_result="진행 상황: 기준선만 측정했고 비교 실행은 아직입니다.",
            verification_status="UNVERIFIED",
            metadata_json={},
        )
        session.add_all([change, validation, complete, incomplete])
        session.flush()

        settings = Settings(
            database_url=database_url,
            vault_dir=tmp_path / "vault",
            knowledge_auto_publish=True,
        )
        counts = finalize_pending_stops(session, settings)
        assert counts["considered"] >= 2
        assert counts["activity_only"] >= 1
        assert counts["candidates"] == 1
        assert counts["published"] == 0
        assert counts["needs_review"] == 1
        generated = session.scalar(
            select(KnowledgeCandidate).where(
                KnowledgeCandidate.metadata_json["source_session_id"].astext == session_id
            )
        )
        assert generated is not None
        assert generated.status == "needs_review"
        assert generated.metadata_json["reported_result_is_evidence"] is False
        assert (
            "local_llm_evidence_validation_required"
            in (generated.metadata_json["quality_gate_reasons"])
        )
        assert "sample-project" in generated.title
        assert (
            session.scalar(
                select(func.count())
                .select_from(EvidenceRecord)
                .where(
                    EvidenceRecord.candidate_id == generated.id,
                    EvidenceRecord.evidence_type == "test_pass",
                )
            )
            == 1
        )
        assert (
            session.scalar(
                select(KnowledgeCase).where(KnowledgeCase.dedup_key == generated.dedup_key)
            )
            is None
        )

        assert finalize_pending_stops(session, settings)["considered"] == 0
        session.rollback()


def test_legacy_patch_validation_evidence_is_retracted_without_deleting_history(
    database_url: str,
):
    engine = create_engine(database_url)
    with Session(engine) as session:
        activity = ActivityEvent(
            event_key=f"legacy-patch-{uuid.uuid4()}",
            session_id=f"legacy-session-{uuid.uuid4()}",
            turn_id="legacy-turn",
            event_type="PostToolUse",
            occurred_at=datetime.now(timezone.utc),
            project_key="legacy-project",
            cwd=r"C:\Dev\Repos\legacy-project",
            tool_name="apply_patch",
            command="*** Update File: tests/test_worker.py",
            exit_code=0,
            changed_files=[r"C:\Dev\Repos\legacy-project\tests\test_worker.py"],
            document_version_ids=[],
            verified_result="observed exit_code=0",
            verification_status="VERIFIED",
            metadata_json={},
        )
        session.add(activity)
        session.flush()
        candidate = create_candidate(
            session,
            category="implementation",
            title="[legacy-project] worker test update",
            problem="worker validation was recorded from an edit payload",
            symptom="an edit payload looked like a test command",
            root_cause="the event classifier matched text without checking the tool",
            solution="accept validation only from command execution tools",
            reported_result="tests passed",
            verified_result="edit exit 0",
            evidence=[
                {
                    "activity_id": activity.id,
                    "evidence_type": "code_change",
                    "claim": "file changed",
                    "exit_code": 0,
                    "verified": True,
                },
                {
                    "activity_id": activity.id,
                    "evidence_type": "test_pass",
                    "claim": "test passed",
                    "exit_code": 0,
                    "verified": True,
                },
            ],
            metadata={"auto_generated": True},
        )
        candidate.evidence_gate_status = "VERIFIED"
        candidate.status = "needs_review"

        report = invalidate_misclassified_execution_evidence(session)

        assert report == {
            "invalidated_evidence": 1,
            "affected_candidates": 1,
            "reclassified_activity_only": 1,
        }
        assert candidate.status == "activity_only"
        assert candidate.evidence_gate_status == "NEEDS_EVIDENCE"
        invalidated = session.scalar(
            select(EvidenceRecord).where(
                EvidenceRecord.candidate_id == candidate.id,
                EvidenceRecord.evidence_type == "test_pass",
            )
        )
        assert invalidated is not None and invalidated.verified is False
        assert invalidated.metadata_json["invalidated_reason"] == (
            "non_execution_tool_misclassified_as_command"
        )
        assert session.get(ActivityEvent, activity.id) is activity
        session.rollback()


def test_late_tool_evidence_reopens_activity_only_stop(database_url: str, tmp_path: Path):
    engine = create_engine(database_url)
    session_id = f"late-session-{uuid.uuid4()}"
    now = datetime.now(timezone.utc)
    settings = Settings(
        database_url=database_url,
        vault_dir=tmp_path / "vault",
        knowledge_auto_publish=True,
    )
    with Session(engine) as session:
        stop = ActivityEvent(
            event_key=f"stop-{uuid.uuid4()}",
            session_id=session_id,
            turn_id="late-turn",
            event_type="Stop",
            occurred_at=now + timedelta(seconds=3),
            project_key="thread-folder",
            cwd=r"C:\Users\kutae\Documents\Codex\thread-folder",
            instruction="late evidence 처리 로직을 구현하고 테스트해",
            changed_files=[],
            document_version_ids=[],
            reported_result=(
                "구현 완료\n"
                "목표: 늦게 도착한 실행 근거를 안전하게 연결합니다.\n"
                "구현 방식: 완료 이벤트를 다시 열어 idempotent하게 평가합니다.\n"
                "검증: pytest PASS"
            ),
            verification_status="UNVERIFIED",
            metadata_json={},
        )
        session.add(stop)
        session.flush()
        candidate, outcome = finalize_stop(session, stop, settings)
        assert candidate is None
        assert outcome == "ACTIVITY_ONLY"
        assert stop.metadata_json["knowledge_pipeline"]["reason"] == ("no_meaningful_file_change")

        envelope_to_activity(
            session,
            {
                "event_id": f"change-{uuid.uuid4()}",
                "event_name": "PostToolUse",
                "session_id": session_id,
                "turn_id": "late-turn",
                "cwd": stop.cwd,
                "tool_name": "apply_patch",
                "payload": {
                    "timestamp": (now + timedelta(seconds=1)).isoformat(),
                    "exit_code": 0,
                    "tool_input": {
                        "input": (
                            "*** Begin Patch\n"
                            "*** Update File: "
                            "C:\\Dev\\Repos\\late-project\\worker.py\n"
                            "*** End Patch\n"
                        )
                    },
                },
            },
        )
        envelope_to_activity(
            session,
            {
                "event_id": f"test-{uuid.uuid4()}",
                "event_name": "PostToolUse",
                "session_id": session_id,
                "turn_id": "late-turn",
                "cwd": stop.cwd,
                "tool_name": "Bash",
                "payload": {
                    "timestamp": (now + timedelta(seconds=2)).isoformat(),
                    "exit_code": 0,
                    "tool_input": {"command": "pytest -q tests/test_worker.py"},
                },
            },
        )
        session.flush()
        assert "knowledge_pipeline" not in stop.metadata_json
        candidate, outcome = finalize_stop(session, stop, settings)
        assert candidate is not None
        assert candidate.status == "needs_review"
        assert outcome == "NEEDS_REVIEW"
        session.rollback()


def test_qualified_error_case_dedup_and_different_cause_relation(database_url: str, tmp_path: Path):
    engine = create_engine(database_url)
    now = datetime.now(timezone.utc)
    settings = Settings(
        database_url=database_url,
        vault_dir=tmp_path / "vault",
        knowledge_auto_publish=True,
    )

    def add_turn(
        session: Session,
        *,
        session_id: str,
        turn_id: str,
        offset: int,
        cause: str,
        resolution: str,
    ) -> ActivityEvent:
        common = {
            "session_id": session_id,
            "turn_id": turn_id,
            "project_key": "thread-folder",
            "cwd": r"C:\Users\kutae\Documents\Codex\thread-folder",
            "document_version_ids": [],
            "metadata_json": {},
        }
        failure = ActivityEvent(
            **common,
            event_key=f"failure-{uuid.uuid4()}",
            event_type="PostToolUse",
            occurred_at=now + timedelta(seconds=offset),
            tool_name="Bash",
            command="pytest -q tests/test_lease.py",
            exit_code=1,
            changed_files=[],
            verified_result="observed exit_code=1",
            verification_status="VERIFIED",
        )
        change = ActivityEvent(
            **common,
            event_key=f"change-{uuid.uuid4()}",
            event_type="PostToolUse",
            occurred_at=now + timedelta(seconds=offset + 1),
            tool_name="apply_patch",
            exit_code=0,
            changed_files=[r"C:\Dev\Repos\lease-project\worker.py"],
            verified_result="observed exit_code=0",
            verification_status="VERIFIED",
        )
        success = ActivityEvent(
            **common,
            event_key=f"success-{uuid.uuid4()}",
            event_type="PostToolUse",
            occurred_at=now + timedelta(seconds=offset + 2),
            tool_name="Bash",
            command="pytest -q tests/test_lease.py",
            exit_code=0,
            changed_files=[],
            verified_result="observed exit_code=0",
            verification_status="VERIFIED",
        )
        stop = ActivityEvent(
            **common,
            event_key=f"stop-{uuid.uuid4()}",
            event_type="Stop",
            occurred_at=now + timedelta(seconds=offset + 3),
            instruction="worker lease 회귀 오류를 수정하고 테스트해",
            changed_files=[],
            reported_result=(
                "worker lease 회귀 오류 수정 완료\n"
                f"원인: {cause}\n"
                f"조치: {resolution}\n"
                "검증: pytest PASS"
            ),
            verified_result="pytest exit=1; apply_patch exit=0; pytest exit=0",
            verification_status="VERIFIED",
        )
        session.add_all([failure, change, success, stop])
        session.flush()
        return stop

    with Session(engine) as session:

        def qualify_and_publish(candidate: KnowledgeCandidate):
            candidate.metadata_json = {
                **(candidate.metadata_json or {}),
                "structured_knowledge": True,
                "approval_policy": "local_llm_evidence_bound",
                "curation_validation_status": "PASS",
            }
            return publish_candidate(session, candidate)

        first_stop = add_turn(
            session,
            session_id=f"error-session-{uuid.uuid4()}",
            turn_id="first",
            offset=0,
            cause="fixture에 lease_expires_at 필드가 없었다",
            resolution="fixture에 만료 시각을 추가했다",
        )
        first, outcome = finalize_stop(session, first_stop, settings)
        assert first is not None and first.status == "needs_review"
        first_case, outcome = qualify_and_publish(first)
        assert outcome == "CREATED_CANONICAL"
        assert first_case is not None

        repeat_stop = add_turn(
            session,
            session_id=f"error-session-{uuid.uuid4()}",
            turn_id="repeat",
            offset=10,
            cause="fixture에 lease_expires_at 필드가 없었다",
            resolution="fixture에 만료 시각을 추가했다",
        )
        repeated, outcome = finalize_stop(session, repeat_stop, settings)
        assert repeated is not None
        repeated_case, outcome = qualify_and_publish(repeated)
        assert outcome == "MERGED_OCCURRENCE"
        assert repeated_case is not None and repeated_case.id == first_case.id
        assert first_case.occurrence_count == 2

        other_stop = add_turn(
            session,
            session_id=f"error-session-{uuid.uuid4()}",
            turn_id="other",
            offset=20,
            cause="테스트 DB 시간이 UTC가 아니었다",
            resolution="테스트 DB timezone을 UTC로 고정했다",
        )
        other, outcome = finalize_stop(session, other_stop, settings)
        assert other is not None
        other_case, outcome = qualify_and_publish(other)
        assert outcome == "CREATED_CANONICAL"
        assert other_case is not None and other_case.id != first_case.id
        relation = session.scalar(
            select(KnowledgeCaseRelation).where(
                KnowledgeCaseRelation.source_case_id == first_case.id,
                KnowledgeCaseRelation.target_case_id == other_case.id,
                KnowledgeCaseRelation.relation_type == "same_symptom_different_cause",
            )
        )
        assert relation is not None
        session.rollback()


def test_generic_auto_case_is_retracted_without_deleting_history(database_url: str, tmp_path: Path):
    engine = create_engine(database_url)
    with Session(engine) as session:
        auto = create_candidate(
            session,
            category="implementation",
            title="[sample] changed files",
            problem="sample implementation work",
            symptom="sample implementation work",
            root_cause=(
                "Observed implementation in sample: 3 meaningful file(s) changed "
                "with successful tool exits."
            ),
            solution="Changed artifacts: worker.py. Observed validation: test command.",
            reported_result="implementation completed",
            verified_result="pytest exit 0",
            evidence=evidence(("code_change", None), ("test_pass", 0)),
            metadata={
                "auto_generated": True,
                "extractor": "deterministic-activity-v1",
            },
        )
        auto.status = "published"
        auto.evidence_gate_status = "VERIFIED"
        case = KnowledgeCase(
            category=auto.category,
            title=auto.title,
            problem=auto.problem,
            symptom=auto.symptom,
            root_cause=auto.root_cause,
            solution=auto.solution,
            dedup_key=auto.dedup_key,
            status="verified",
        )
        session.add(case)
        session.flush()
        session.add(KnowledgeOccurrence(case_id=case.id, candidate_id=auto.id))
        session.flush()

        preview = review_low_quality_auto_cases(session, vault_dir=tmp_path / "vault", apply=False)
        assert any(item["case_id"] == str(case.id) for item in preview)
        assert case.status == "verified"

        applied = review_low_quality_auto_cases(session, vault_dir=tmp_path / "vault", apply=True)
        assert any(item["case_id"] == str(case.id) for item in applied)
        assert case.status == "retired"
        assert auto.status == "needs_review"
        assert auto.evidence_gate_status == "VERIFIED"
        assert auto.metadata_json["quality_gate_status"] == "NEEDS_REVIEW"
        session.rollback()


def test_activity_detail_rollup_preserves_failures_and_evidence(database_url: str):
    engine = create_engine(database_url)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        old_success = ActivityEvent(
            event_key=f"old-success-{uuid.uuid4()}",
            session_id=f"retention-{uuid.uuid4()}",
            turn_id="old",
            event_type="PostToolUse",
            occurred_at=now - timedelta(days=31),
            tool_name="Bash",
            command="git status",
            exit_code=0,
            changed_files=[],
            document_version_ids=[],
            verification_status="VERIFIED",
            metadata_json={},
        )
        old_failure = ActivityEvent(
            event_key=f"old-failure-{uuid.uuid4()}",
            session_id=old_success.session_id,
            turn_id="old",
            event_type="PostToolUse",
            occurred_at=now - timedelta(days=31),
            tool_name="Bash",
            command="pytest",
            exit_code=1,
            changed_files=[],
            document_version_ids=[],
            verification_status="VERIFIED",
            metadata_json={},
        )
        session.add_all([old_success, old_failure])
        session.flush()

        assert roll_up_activity_details(session, retention_days=30, now=now) == 1
        assert old_success.metadata_json["retention_state"] == "rolled_up"
        assert "retention_state" not in old_failure.metadata_json
        assert roll_up_activity_details(session, retention_days=30, now=now) == 0
        session.rollback()
