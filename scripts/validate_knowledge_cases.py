import json
from datetime import datetime, timezone

from lkp.db import SessionLocal
from lkp.models import (
    ActivityEvent,
    IngestJob,
    JobStatus,
    KnowledgeCandidate,
    KnowledgeCase,
    KnowledgeCaseRelation,
    SourceRoot,
)
from lkp_indexer.knowledge import create_candidate, publish_candidate
from sqlalchemy import func, select


def verified_evidence(*items: tuple[str, str, int | None]) -> list[dict]:
    return [
        {
            "evidence_type": kind,
            "claim": claim,
            "locator": "docs/evidence/validation-report.md",
            "exit_code": exit_code,
            "verified": True,
            "metadata": {"data_scope": "validation"},
        }
        for kind, claim, exit_code in items
    ]


def ensure_activity(session, *, event_key: str, reported: str, verified: str | None):
    row = session.scalar(
        select(ActivityEvent).where(ActivityEvent.event_key == event_key)
    )
    if row:
        return row
    row = ActivityEvent(
        event_key=event_key,
        session_id="validation-scenarios",
        turn_id=event_key,
        event_type="ValidationScenario",
        occurred_at=datetime.now(timezone.utc),
        project_key="local-knowledge-portal",
        cwd=r"C:\Dev\Repos\local-knowledge-portal",
        reported_result=reported,
        verified_result=verified,
        verification_status="VERIFIED" if verified else "UNVERIFIED",
        metadata_json={"data_scope": "validation"},
    )
    session.add(row)
    session.flush()
    return row


def ensure_candidate(session, title: str, **values):
    existing = session.scalar(
        select(KnowledgeCandidate).where(KnowledgeCandidate.title == title)
    )
    if existing:
        return existing
    row = create_candidate(session, title=title, **values)
    row.metadata_json = {"data_scope": "validation"}
    return row


def main() -> None:
    with SessionLocal() as session:
        css = ensure_activity(
            session,
            event_key="scenario-css-activity-only-v1",
            reported="CSS focus-ring spacing changed",
            verified="Next.js production build exit=0",
        )
        unverified = ensure_activity(
            session,
            event_key="scenario-reported-without-evidence-v1",
            reported="Codex reported success",
            verified=None,
        )
        validation_root = session.scalar(
            select(SourceRoot).where(SourceRoot.data_scope == "validation")
        )
        failed_job = session.scalar(
            select(IngestJob).where(
                IngestJob.idempotency_key == "scenario-failed-job-v1"
            )
        )
        if validation_root and failed_job is None:
            failed_job = IngestJob(
                idempotency_key="scenario-failed-job-v1",
                source_root_id=validation_root.id,
                canonical_path=r"E:\LocalKnowledgePortal\ingest\validation\missing.md",
                job_type="validation_failure",
                status=JobStatus.failed,
                attempt_count=1,
                max_attempts=5,
                error_type="ValidationFailure",
                error_message="Intentional isolated failed job for retry E2E",
                error_details={"data_scope": "validation"},
            )
            session.add(failed_job)
        dead_letter = session.scalar(
            select(IngestJob).where(
                IngestJob.idempotency_key == "scenario-dead-letter-job-v1"
            )
        )
        if validation_root and dead_letter is None:
            dead_letter = IngestJob(
                idempotency_key="scenario-dead-letter-job-v1",
                source_root_id=validation_root.id,
                canonical_path=r"E:\LocalKnowledgePortal\ingest\validation\dead-letter.md",
                job_type="validation_dead_letter",
                status=JobStatus.dead_letter,
                attempt_count=5,
                max_attempts=5,
                error_type="ValidationDeadLetter",
                error_message="Intentional isolated dead letter for retry E2E",
                error_details={"data_scope": "validation"},
            )
            session.add(dead_letter)
        common = {
            "category": "error_resolution",
            "problem": "collector idempotency integration test failed",
            "symptom": "collect_file returned false on the first assertion",
            "root_cause": "the test reused a deterministic hook event identity from an earlier run",
            "solution": "generate a unique session identity for every integration test run",
            "reported_result": "test fixed",
            "verified_result": "integration suite passed",
            "evidence": verified_evidence(
                ("command_failure", "pytest observed one failed test", 1),
                ("code_change", "test now uses uuid4 session identity", None),
                ("test_pass", "integration suite passed", 0),
            ),
        }
        first = ensure_candidate(session, "Collector duplicate identity fix", **common)
        repeated = ensure_candidate(session, "Collector duplicate identity recurrence", **common)
        different = ensure_candidate(
            session,
            "Collector false return caused by transaction ordering",
            category="error_resolution",
            problem="collector idempotency integration test failed",
            symptom="collect_file returned false on the first assertion",
            root_cause="a prior transaction had not been rolled back before the assertion",
            solution="isolate the transaction and use a fresh database session",
            reported_result="test fixed",
            verified_result="integration suite passed",
            evidence=verified_evidence(
                ("command_failure", "pytest observed one failed test", 1),
                ("code_change", "database session isolation applied", None),
                ("test_pass", "integration suite passed", 0),
            ),
        )
        uncertain = ensure_candidate(
            session,
            "Possible collector duplicate requiring review",
            category="error_resolution",
            problem="collector idempotency integration test failed",
            symptom="intermittent collector validation error",
            root_cause="the test reused a deterministic hook event identity from an earlier run",
            solution="generate a unique session identity and rerun the integration test",
            reported_result="possibly the same fix",
            verified_result="integration suite passed",
            evidence=verified_evidence(
                ("command_failure", "pytest observed one failed test", 1),
                ("code_change", "unique session identity applied", None),
                ("test_pass", "integration suite passed", 0),
            ),
        )
        unmeasured = ensure_candidate(
            session,
            "Unmeasured performance claim",
            category="performance",
            problem="portal search was described as faster",
            symptom="no baseline measurement exists",
            root_cause="the claim came from a reported result only",
            solution="capture before and after metrics before publishing",
            reported_result="faster",
            verified_result=None,
            evidence=verified_evidence(
                ("code_change", "search code changed", None),
                ("test_pass", "functional search test passed", 0),
            ),
        )
        measured = ensure_candidate(
            session,
            "Watcher burst coalescing under repeated saves",
            category="performance",
            problem="rapid saves can create an excessive queue burst",
            symptom="100 events for one canonical path",
            root_cause="the watcher receives repeated notifications for a single editor save burst",
            solution="merge each debounce batch by canonical path before enqueue",
            reported_result="event burst reduced",
            verified_result="100 input events produced one merged path",
            evidence=verified_evidence(
                ("performance_before", "naive input count=100", None),
                ("performance_after", "merged canonical path count=1", None),
                ("load_cause", "100 repeated events targeted one path", None),
            ),
        )
        implementation = ensure_candidate(
            session,
            "Atomic Codex raw spool implementation",
            category="implementation",
            problem="Codex events must survive portal downtime without direct API calls",
            symptom="direct hook ingestion coupled Codex to database availability",
            root_cause="the legacy Stop hook invoked the database-backed capture pipeline",
            solution="write redacted idempotent envelopes atomically to the E drive spool",
            reported_result="raw spool implemented",
            verified_result="hook spool unit suite and live six-event collection passed",
            evidence=verified_evidence(
                ("code_change", "hook_spool and collector modules added", None),
                ("test_pass", "hook spool unit tests passed", 0),
            ),
        )
        custom_success = ensure_candidate(
            session,
            "Idempotent global Codex hook merge",
            category="custom_success",
            problem="global Codex hooks must preserve existing user configuration",
            symptom="only the legacy Stop hook was installed",
            root_cause="the prior installer replaced no events beyond Stop",
            solution="merge six managed hook entries and back up existing config before changes",
            reported_result="global hooks installed",
            verified_result="two installer runs produced the same hooks.json SHA-256",
            evidence=verified_evidence(
                ("code_change", "merge-safe PowerShell installer implemented", None),
                ("command_success", "idempotency hash comparison returned true", 0),
            ),
        )
        operations = ensure_candidate(
            session,
            "PostgreSQL outage raw-spool recovery",
            category="operations",
            problem="activity collection must survive a PostgreSQL restart",
            symptom="database was intentionally stopped while a Codex instruction arrived",
            root_cause="the collector could not commit while PostgreSQL was unavailable",
            solution="retain the claimed envelope and retry collection after database recovery",
            reported_result="outage recovery completed",
            verified_result="same session instruction was present after PostgreSQL became healthy",
            evidence=verified_evidence(
                ("incident_failure", "PostgreSQL container stopped and readiness failed", None),
                ("recovery_success", "raw event collected after container restart", 0),
            ),
        )
        outcomes = {}
        for row in (
            first,
            repeated,
            different,
            uncertain,
            unmeasured,
            measured,
            implementation,
            custom_success,
            operations,
        ):
            case, outcome = publish_candidate(session, row)
            outcomes[row.title] = outcome
            if case:
                case.metadata_json = {
                    **case.metadata_json,
                    "data_scope": "validation",
                }
        session.commit()
        case_count = session.scalar(
            select(func.count()).select_from(KnowledgeCase)
        )
        relation_count = session.scalar(
            select(func.count()).select_from(KnowledgeCaseRelation)
        )
        print(
            json.dumps(
                {
                    "css_activity_id": str(css.id),
                    "css_created_case": False,
                    "unverified_activity_id": str(unverified.id),
                    "unverified_status": unverified.verification_status,
                    "failed_job_id": str(failed_job.id) if failed_job else None,
                    "dead_letter_job_id": str(dead_letter.id) if dead_letter else None,
                    "outcomes": outcomes,
                    "canonical_cases": case_count,
                    "relations": relation_count,
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
