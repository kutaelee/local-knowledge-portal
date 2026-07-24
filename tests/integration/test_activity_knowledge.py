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
from lkp_indexer.case_pages import materialize_case
from lkp_indexer.hook_collector import collect_file, envelope_to_activity
from lkp_indexer.hook_spool import spool
from lkp_indexer.knowledge import create_candidate, publish_candidate
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
        "evidence": evidence(
            ("command_failure", 1), ("code_change", None), ("test_pass", 0)
        ),
    }
    values.update(overrides)
    return create_candidate(session, **values)


def test_collector_separates_reported_and_verified(database_url: str, tmp_path: Path):
    engine = create_engine(database_url)
    session_id = f"s-collector-{uuid.uuid4()}"
    instruction_raw = (
        '{"session_id":"'
        + session_id
        + '","turn_id":"t-collector",'
        '"hook_event_name":"UserPromptSubmit","cwd":"C:\\\\Dev\\\\Repos\\\\sample",'
        '"prompt":"테스트 실패 원인을 수정하고 다시 검증해줘"}'
    ).encode()
    stop_raw = (
        '{"session_id":"'
        + session_id
        + '","turn_id":"t-collector",'
        '"hook_event_name":"Stop","cwd":"C:\\\\Dev\\\\Repos\\\\sample",'
        '"last_assistant_message":"all tests pass"}'
    ).encode()
    instruction_path = spool(
        instruction_raw, tmp_path / "spool", tmp_path / "fallback"
    )
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
        assert (
            session.scalar(select(func.count()).select_from(GeneratedPage)) == 1
        )
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


def test_global_activity_turns_become_evidence_gated_cases(
    database_url: str, tmp_path: Path
):
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
            reported_result="worker 재시작 안전장치 구현 완료\n\n회귀 테스트 PASS",
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
        assert counts["published"] == 1
        assert counts["needs_review"] == 0
        generated = session.scalar(
            select(KnowledgeCandidate).where(
                KnowledgeCandidate.metadata_json["source_session_id"].astext
                == session_id
            )
        )
        assert generated is not None
        assert generated.status == "published"
        assert generated.metadata_json["reported_result_is_evidence"] is False
        assert "sample-project" in generated.title
        case = session.scalar(
            select(KnowledgeCase).where(KnowledgeCase.dedup_key == generated.dedup_key)
        )
        assert case is not None
        page = next((tmp_path / "vault" / "_generated" / "Knowledge-Cases").glob("*.md"))
        content = page.read_text(encoding="utf-8")
        assert "보고된 결과 / Reported outcome" in content
        assert "그 자체는 검증 근거가 아닙니다" in content
        assert "회귀 테스트 PASS" in content

        assert finalize_pending_stops(session, settings)["considered"] == 0
        session.rollback()


def test_late_tool_evidence_reopens_activity_only_stop(
    database_url: str, tmp_path: Path
):
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
            reported_result="late evidence 처리 구현 완료, 테스트 PASS",
            verification_status="UNVERIFIED",
            metadata_json={},
        )
        session.add(stop)
        session.flush()
        candidate, outcome = finalize_stop(session, stop, settings)
        assert candidate is None
        assert outcome == "ACTIVITY_ONLY"
        assert stop.metadata_json["knowledge_pipeline"]["reason"] == (
            "no_meaningful_file_change"
        )

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
        assert candidate.status == "published"
        assert outcome == "CREATED_CANONICAL"
        session.rollback()


def test_automatic_error_case_dedup_and_different_cause_relation(
    database_url: str, tmp_path: Path
):
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
        first_stop = add_turn(
            session,
            session_id=f"error-session-{uuid.uuid4()}",
            turn_id="first",
            offset=0,
            cause="fixture에 lease_expires_at 필드가 없었다",
            resolution="fixture에 만료 시각을 추가했다",
        )
        first, outcome = finalize_stop(session, first_stop, settings)
        assert first is not None and first.status == "published"
        assert outcome == "CREATED_CANONICAL"
        first_case = session.scalar(
            select(KnowledgeCase).where(KnowledgeCase.dedup_key == first.dedup_key)
        )
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
        assert outcome == "MERGED_OCCURRENCE"
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
        assert outcome == "CREATED_CANONICAL"
        other_case = session.scalar(
            select(KnowledgeCase).where(KnowledgeCase.dedup_key == other.dedup_key)
        )
        assert other_case is not None and other_case.id != first_case.id
        relation = session.scalar(
            select(KnowledgeCaseRelation).where(
                KnowledgeCaseRelation.source_case_id == first_case.id,
                KnowledgeCaseRelation.target_case_id == other_case.id,
                KnowledgeCaseRelation.relation_type
                == "same_symptom_different_cause",
            )
        )
        assert relation is not None
        session.rollback()
