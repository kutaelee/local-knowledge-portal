import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from lkp.models import (
    ActivityEvent,
    EvidenceRecord,
    KnowledgeCase,
    KnowledgeCaseRelation,
    KnowledgeOccurrence,
)
from lkp_indexer.hook_collector import collect_file
from lkp_indexer.hook_spool import spool
from lkp_indexer.knowledge import create_candidate, publish_candidate
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


def test_evidence_gate_dedup_and_relationships(database_url: str):
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
