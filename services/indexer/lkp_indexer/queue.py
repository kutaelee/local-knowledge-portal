import math
import uuid
from datetime import datetime, timedelta, timezone

from lkp.models import IngestJob, JobStatus
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session


def enqueue(
    session: Session,
    *,
    key: str,
    source_root_id: uuid.UUID,
    canonical_path: str,
    job_type: str = "index",
    max_attempts: int = 5,
) -> IngestJob | None:
    statement = (
        insert(IngestJob)
        .values(
            idempotency_key=key,
            source_root_id=source_root_id,
            canonical_path=canonical_path,
            job_type=job_type,
            status=JobStatus.pending,
            max_attempts=max_attempts,
        )
        .on_conflict_do_nothing(index_elements=["idempotency_key"])
        .returning(IngestJob.id)
    )
    job_id = session.execute(statement).scalar_one_or_none()
    return session.get(IngestJob, job_id) if job_id else None


def lease(session: Session, worker_id: str, lease_seconds: int) -> IngestJob | None:
    statement = text(
        """
        WITH candidate AS (
          SELECT id FROM ingest_job
          WHERE (
            (status IN ('pending', 'failed') AND available_at <= now())
            OR (status IN ('leased', 'processing') AND lease_expires_at < now())
          )
          AND attempt_count < max_attempts
          ORDER BY priority ASC, available_at ASC, created_at ASC
          FOR UPDATE SKIP LOCKED LIMIT 1
        )
        UPDATE ingest_job j SET
          status = 'leased', leased_by = :worker_id,
          lease_expires_at = now() + make_interval(secs => :lease_seconds),
          started_at = coalesce(started_at, now()), updated_at = now(),
          attempt_count = attempt_count + 1
        FROM candidate WHERE j.id = candidate.id
        RETURNING j.id
        """
    )
    job_id = session.execute(
        statement, {"worker_id": worker_id, "lease_seconds": lease_seconds}
    ).scalar_one_or_none()
    return session.get(IngestJob, job_id) if job_id else None


def retry_delay(attempt: int, base: int = 2, cap: int = 300) -> int:
    return min(cap, int(base * math.pow(2, max(0, attempt - 1))))


def fail(session: Session, job: IngestJob, exc: Exception) -> None:
    now = datetime.now(timezone.utc)
    job.error_type = type(exc).__name__
    job.error_message = str(exc)[:4000]
    job.finished_at = now
    job.updated_at = now
    if job.attempt_count >= job.max_attempts:
        job.status = JobStatus.dead_letter
    else:
        job.status = JobStatus.failed
        job.available_at = now + timedelta(seconds=retry_delay(job.attempt_count))


def finish(session: Session, job: IngestJob) -> None:
    job.status = JobStatus.succeeded
    job.finished_at = datetime.now(timezone.utc)
    job.lease_expires_at = None
    job.updated_at = job.finished_at


def retry_as_new(session: Session, job_id: uuid.UUID) -> IngestJob:
    original = session.scalar(select(IngestJob).where(IngestJob.id == job_id))
    if not original:
        raise LookupError("job not found")
    key = f"retry:{original.id}:{uuid.uuid4()}"
    clone = IngestJob(
        idempotency_key=key,
        source_root_id=original.source_root_id,
        document_id=original.document_id,
        canonical_path=original.canonical_path,
        job_type=original.job_type,
        priority=original.priority,
        status=JobStatus.pending,
        max_attempts=original.max_attempts,
        error_details={"retry_of": str(original.id)},
    )
    session.add(clone)
    session.flush()
    return clone
