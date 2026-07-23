import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from lkp.models import Document, DocumentState, IngestJob, JobStatus, SourceRoot
from lkp.settings import Settings
from lkp_indexer.paths import idempotency_key
from lkp_indexer.queue import enqueue, lease
from lkp_indexer.reconcile import reconcile_root
from lkp_indexer.watcher import watch_root
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

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


def test_expired_lease_is_recovered(database_url: str, tmp_path: Path):
    engine = create_engine(database_url)
    with Session(engine) as session:
        root = SourceRoot(
            name=f"lease-{uuid.uuid4()}",
            canonical_path=str(tmp_path),
            source_type="validation",
            data_scope="validation",
            read_only=True,
            enabled=True,
            include_patterns=["**/*"],
            exclude_patterns=[],
        )
        session.add(root)
        session.flush()
        job = enqueue(
            session,
            key=idempotency_key(str(root.id), str(tmp_path / "lease.md"), 1, 1),
            source_root_id=root.id,
            canonical_path=str(tmp_path / "lease.md"),
            priority=-100,
        )
        session.commit()
        assert job is not None
        first = lease(session, "worker-that-crashed", 30)
        assert first is not None
        session.commit()
        first.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
        recovered = lease(session, "recovery-worker", 30)
        assert recovered is not None
        assert recovered.id == first.id
        assert recovered.attempt_count == 2
        assert recovered.leased_by == "recovery-worker"
        recovered.status = JobStatus.cancelled
        session.commit()


@pytest.mark.asyncio
async def test_watcher_coalesces_create_modify_rename_delete(database_url: str, tmp_path: Path):
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        root = SourceRoot(
            name=f"watch-{uuid.uuid4()}",
            canonical_path=str(tmp_path),
            source_type="validation",
            data_scope="validation",
            read_only=True,
            enabled=True,
            include_patterns=["**/*"],
            exclude_patterns=[],
        )
        session.add(root)
        session.commit()
        session.expunge(root)
    stop = asyncio.Event()
    task = asyncio.create_task(
        watch_root(
            factory,
            root,
            debounce_ms=100,
            force_polling=False,
            stability_seconds=0.05,
            stop_event=stop,
        )
    )
    await asyncio.sleep(0.4)
    created = tmp_path / "created.md"
    created.write_text("# first", encoding="utf-8")
    await asyncio.sleep(0.5)
    created.write_text("# second", encoding="utf-8")
    await asyncio.sleep(0.5)
    renamed = tmp_path / "renamed.md"
    created.rename(renamed)
    await asyncio.sleep(0.5)
    renamed.unlink()
    await asyncio.sleep(0.5)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    with factory() as session:
        jobs = session.scalars(
            select(IngestJob).where(IngestJob.source_root_id == root.id)
        ).all()
        job_types = {job.job_type for job in jobs}
        assert "watch_index" in job_types
        assert "watch_delete" in job_types
        assert len(jobs) <= 5
        assert all(job.priority <= 20 for job in jobs)
        for job in jobs:
            if job.status in {JobStatus.pending, JobStatus.leased, JobStatus.processing}:
                job.status = JobStatus.cancelled
        session.commit()


def test_reconciliation_recovers_missed_delete(database_url: str, tmp_path: Path):
    engine = create_engine(database_url)
    settings = Settings(database_url=database_url)
    absent = tmp_path / "missed.md"
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        root = SourceRoot(
            name=f"reconcile-{uuid.uuid4()}",
            canonical_path=str(tmp_path),
            source_type="validation",
            data_scope="validation",
            read_only=True,
            enabled=True,
            include_patterns=["**/*"],
            exclude_patterns=[],
        )
        session.add(root)
        session.flush()
        document = Document(
            source_root_id=root.id,
            canonical_path=str(absent),
            relative_path="missed.md",
            filename="missed.md",
            extension=".md",
            mime_type="text/markdown",
            project_key="fixture",
            parent_path=".",
            size_bytes=1,
            modified_at_fs=now,
            state=DocumentState.active,
        )
        session.add(document)
        session.commit()
        stats = reconcile_root(session, root, settings)
        session.commit()
        assert stats.missing == 1
        recovered = session.scalar(
            select(IngestJob).where(
                IngestJob.source_root_id == root.id,
                IngestJob.job_type == "reconcile_delete",
                IngestJob.status == JobStatus.pending,
            )
        )
        assert recovered is not None
        recovered.status = JobStatus.cancelled
        session.commit()
