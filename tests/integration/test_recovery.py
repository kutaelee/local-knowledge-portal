import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from lkp.cleanup_derived import cleanup_ignored
from lkp.models import (
    ActivityEvent,
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentState,
    DocumentVersion,
    HookSpoolEvent,
    IngestJob,
    JobStatus,
    SourceRoot,
)
from lkp.settings import Settings
from lkp_indexer.hook_collector import collect_file
from lkp_indexer.paths import idempotency_key
from lkp_indexer.purpose_migration import migrate_purpose_scope
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


def test_reconciliation_quarantines_newly_ignored_documents(
    database_url: str, tmp_path: Path
):
    engine = create_engine(database_url)
    settings = Settings(database_url=database_url)
    generated = tmp_path / "tokenizer_configs" / "model" / "merges.txt"
    generated.parent.mkdir(parents=True)
    generated.write_text("generated merge records", encoding="utf-8")
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        root = SourceRoot(
            name=f"ignore-{uuid.uuid4()}",
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
            canonical_path=str(generated),
            relative_path="tokenizer_configs/model/merges.txt",
            filename="merges.txt",
            extension=".txt",
            mime_type="text/plain",
            project_key="fixture",
            parent_path="tokenizer_configs/model",
            size_bytes=generated.stat().st_size,
            modified_at_fs=now,
            state=DocumentState.active,
        )
        session.add(document)
        session.commit()
        stats = reconcile_root(session, root, settings)
        session.commit()
        session.refresh(document)
        assert stats.ignored == 1
        assert document.state == DocumentState.ignored


def test_ignored_cleanup_removes_only_derived_rows(
    database_url: str, tmp_path: Path
):
    engine = create_engine(database_url)
    source = tmp_path / "tokenizer_configs" / "model" / "vocab.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"generated": true}', encoding="utf-8")
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        root = SourceRoot(
            name=f"cleanup-{uuid.uuid4()}",
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
            canonical_path=str(source),
            relative_path="tokenizer_configs/model/vocab.json",
            filename="vocab.json",
            extension=".json",
            mime_type="application/json",
            project_key="fixture",
            parent_path="tokenizer_configs/model",
            size_bytes=source.stat().st_size,
            modified_at_fs=now,
            state=DocumentState.ignored,
        )
        session.add(document)
        session.flush()
        version = DocumentVersion(
            document_id=document.id,
            content_hash="a" * 64,
            byte_size=source.stat().st_size,
            created_at_fs=now,
            modified_at_fs=now,
            parser_version="test",
            chunker_version="test",
            line_count=1,
            metadata_json={},
            change_type="created",
            diff_summary={},
        )
        session.add(version)
        session.flush()
        chunk = DocumentChunk(
            document_version_id=version.id,
            chunk_index=0,
            chunk_type="record",
            start_line=1,
            end_line=1,
            content='{"generated": true}',
            content_hash="b" * 64,
            token_estimate=4,
            metadata_json={},
        )
        session.add(chunk)
        session.flush()
        embedding = ChunkEmbedding(
            chunk_id=chunk.id,
            embedding_revision="test-d1024-v1",
            provider="deterministic",
            model="fixture",
            model_digest="fixture",
            dimension=1024,
            embedding=[0.0] * 1024,
        )
        job = IngestJob(
            idempotency_key=f"cleanup-{uuid.uuid4()}",
            source_root_id=root.id,
            document_id=document.id,
            canonical_path=str(source),
            job_type="validation",
            status=JobStatus.succeeded,
            available_at=now,
        )
        session.add_all([embedding, job])
        session.commit()
        document_id = document.id
        version_id = version.id
        chunk_id = chunk.id
        job_id = job.id

    result = cleanup_ignored(
        apply=True,
        session_factory=sessionmaker(bind=engine, expire_on_commit=False),
    )
    assert result["documents"] >= 1
    assert source.is_file()
    with Session(engine) as session:
        assert session.get(Document, document_id) is None
        assert session.get(DocumentVersion, version_id) is None
        assert session.get(DocumentChunk, chunk_id) is None
        assert session.scalar(
            select(ChunkEmbedding).where(ChunkEmbedding.chunk_id == chunk_id)
        ) is None
        preserved_job = session.get(IngestJob, job_id)
        assert preserved_job is not None
        assert preserved_job.document_id is None


def test_low_signal_hook_is_not_persisted(database_url: str, tmp_path: Path):
    engine = create_engine(database_url)
    event_id = uuid.uuid4().hex
    session_id = f"low-signal-{uuid.uuid4()}"
    envelope = {
        "event_id": event_id,
        "event_name": "UserPromptSubmit",
        "session_id": session_id,
        "turn_id": "1",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload_hash": "c" * 64,
        "payload_bytes": 100,
        "payload": {
            "prompt": "오늘 확인한 문서 내용을 간단하게 다시 설명해 주세요",
        },
    }
    path = tmp_path / f"{event_id}.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with Session(engine) as session:
        assert collect_file(session, path) is True
        session.commit()
        assert session.get(HookSpoolEvent, event_id) is None
        assert session.scalar(
            select(ActivityEvent).where(ActivityEvent.session_id == session_id)
        ) is None


def test_purpose_migration_reclassifies_projects_and_keeps_doc_vectors(
    database_url: str, tmp_path: Path
):
    engine = create_engine(database_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    source_root = tmp_path / "src"
    repository = source_root / "ai" / "ExampleRepo"
    (repository / ".git").mkdir(parents=True)
    code_path = repository / "src" / "model.py"
    doc_path = repository / "README.md"
    code_path.parent.mkdir(parents=True)
    code_path.write_text("def model():\n    return 1\n", encoding="utf-8")
    doc_path.write_text("# Example\nReusable design", encoding="utf-8")
    now = datetime.now(timezone.utc)
    with factory() as session:
        root = SourceRoot(
            name=f"purpose-{uuid.uuid4()}",
            canonical_path=str(source_root),
            source_type="repositories",
            data_scope="validation",
            read_only=True,
            enabled=True,
            include_patterns=["**/*"],
            exclude_patterns=[],
        )
        session.add(root)
        session.flush()
        chunk_ids: dict[str, uuid.UUID] = {}
        document_ids: dict[str, uuid.UUID] = {}
        for index, path in enumerate((code_path, doc_path)):
            relative = path.relative_to(source_root).as_posix()
            document = Document(
                source_root_id=root.id,
                canonical_path=str(path),
                relative_path=relative,
                filename=path.name,
                extension=path.suffix,
                mime_type="text/plain",
                project_key="ai",
                project_relative_path=relative,
                parent_path=path.parent.relative_to(source_root).as_posix(),
                size_bytes=path.stat().st_size,
                modified_at_fs=now,
                state=DocumentState.active,
            )
            session.add(document)
            session.flush()
            version = DocumentVersion(
                document_id=document.id,
                content_hash=str(index) * 64,
                byte_size=path.stat().st_size,
                created_at_fs=now,
                modified_at_fs=now,
                parser_version="test",
                chunker_version="test",
                line_count=2,
                metadata_json={"embedding_status": "complete"},
                change_type="created",
                diff_summary={},
            )
            session.add(version)
            session.flush()
            chunk = DocumentChunk(
                document_version_id=version.id,
                chunk_index=0,
                chunk_type="section",
                start_line=1,
                end_line=2,
                content=path.read_text(encoding="utf-8"),
                content_hash=str(index + 2) * 64,
                token_estimate=8,
                metadata_json={},
            )
            session.add(chunk)
            session.flush()
            session.add(
                ChunkEmbedding(
                    chunk_id=chunk.id,
                    embedding_revision="test-d1024-v1",
                    provider="deterministic",
                    model="fixture",
                    model_digest="fixture",
                    dimension=1024,
                    embedding=[float(index)] * 1024,
                )
            )
            document.current_version_id = version.id
            document.current_content_hash = version.content_hash
            chunk_ids[path.name] = chunk.id
            document_ids[path.name] = document.id
        session.commit()

    result = migrate_purpose_scope(
        apply=True,
        repository_mode="docs_only",
        session_factory=factory,
    )
    assert result["project_documents_reclassified"] >= 2
    with factory() as session:
        code_document = session.get(Document, document_ids["model.py"])
        doc_document = session.get(Document, document_ids["README.md"])
        assert code_document is not None
        assert doc_document is not None
        assert code_document.project_key == "ExampleRepo"
        assert code_document.project_relative_path == "src/model.py"
        assert doc_document.project_key == "ExampleRepo"
        assert doc_document.project_relative_path == "README.md"
        assert session.scalar(
            select(ChunkEmbedding).where(
                ChunkEmbedding.chunk_id == chunk_ids["model.py"]
            )
        ) is None
        assert session.scalar(
            select(ChunkEmbedding).where(
                ChunkEmbedding.chunk_id == chunk_ids["README.md"]
            )
        ) is not None
