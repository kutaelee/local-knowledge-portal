import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from lkp.db import get_db
from lkp.main import app
from lkp.models import (
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentTag,
    DocumentVersion,
    IngestJob,
    JobStatus,
    SourceRoot,
    Tag,
)
from lkp.settings import Settings
from lkp_indexer.embedding import DeterministicTestEmbedder
from lkp_indexer.queue import lease
from lkp_indexer.scanner import scan_root
from lkp_indexer.worker import process_job
from sqlalchemy import create_engine, delete, func, select, update
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url():
    value = os.getenv("LKP_TEST_DATABASE_URL")
    if not value:
        pytest.skip("LKP_TEST_DATABASE_URL is not set")
    return value


def test_fixture_pipeline_is_idempotent(database_url: str, tmp_path: Path):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    fixture = Path("tests/fixtures/sample/README.md").resolve()
    root_path = tmp_path / "source"
    root_path.mkdir()
    copied = root_path / "README.md"
    copied.write_text(
        "---\n"
        "managed: true\n"
        "project: fixture-project\n"
        "tags: [situation:operations, platform:wsl2]\n"
        "knowledge_value_tier: promote\n"
        "knowledge_value_labels: [knowledge-value:promote]\n"
        "---\n" + fixture.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    settings = Settings(
        database_url=database_url,
        embedding_provider="deterministic-test",
        embedding_revision="test-d1024-v1",
        embedding_dimension=1024,
    )
    with Session(engine) as session:
        initial_documents = session.scalar(select(func.count()).select_from(Document))
        initial_versions = session.scalar(select(func.count()).select_from(DocumentVersion))
        initial_chunks = session.scalar(select(func.count()).select_from(DocumentChunk))
        initial_embeddings = session.scalar(select(func.count()).select_from(ChunkEmbedding))
        root = SourceRoot(
            name="fixture",
            canonical_path=str(root_path.resolve()),
            source_type="repositories",
            read_only=True,
            enabled=True,
            include_patterns=["**/*"],
            exclude_patterns=[],
        )
        session.add(root)
        session.commit()
        assert scan_root(session, root, settings.max_file_bytes).queued == 1
        session.execute(
            update(IngestJob)
            .where(
                IngestJob.source_root_id == root.id,
                IngestJob.status == JobStatus.pending,
            )
            .values(priority=-100)
        )
        session.commit()
        job = lease(session, "test-worker", 30)
        assert job is not None
        process_job(session, job, settings, DeterministicTestEmbedder(1024), "test-worker")
        session.commit()
        assert session.scalar(select(func.count()).select_from(Document)) == initial_documents + 1
        assert (
            session.scalar(select(func.count()).select_from(DocumentVersion))
            == initial_versions + 1
        )
        document = session.scalar(
            select(Document).where(Document.canonical_path == str(copied))
        )
        assert document is not None
        document.project_key = "stale-generated-folder"
        session.execute(delete(DocumentTag).where(DocumentTag.document_id == document.id))
        session.commit()
        stat = copied.stat()
        os.utime(copied, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
        assert scan_root(session, root, settings.max_file_bytes).queued == 1
        session.commit()
        metadata_job = lease(session, "test-worker", 30)
        assert metadata_job is not None
        process_job(
            session,
            metadata_job,
            settings,
            DeterministicTestEmbedder(1024),
            "test-worker",
        )
        session.commit()
        session.refresh(document)
        assert document.project_key == "fixture-project"
        restored_tags = set(
            session.scalars(
                select(Tag.name)
                .join(DocumentTag, DocumentTag.tag_id == Tag.id)
                .where(DocumentTag.document_id == document.id)
            )
        )
        assert "project:fixture-project" in restored_tags
        assert "platform:wsl2" in restored_tags
        assert (
            session.scalar(select(func.count()).select_from(DocumentVersion))
            == initial_versions + 1
        )
        assert session.scalar(select(func.count()).select_from(DocumentChunk)) >= initial_chunks + 3
        assert (
            session.scalar(select(func.count()).select_from(ChunkEmbedding))
            >= initial_embeddings + 3
        )

        def override_db():
            yield session

        app.dependency_overrides[get_db] = override_db
        try:
            response = TestClient(app).get(
                "/api/v1/search", params={"q": "PostgreSQL", "top_k": 100}
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["results"]
            assert payload["results"][0]["provenance"]["start_line"] > 0
            assert any(
                result["provenance"]["canonical_path"] == str(copied)
                for result in payload["results"]
            )
            filtered = TestClient(app).post(
                "/api/v1/search/hybrid",
                json={
                    "query": "PostgreSQL",
                    "mode": "keyword",
                    "top_k": 100,
                    "project": "fixture-project",
                    "tags": ["situation:operations", "platform:wsl2"],
                    "tag_mode": "all",
                },
            )
            assert filtered.status_code == 200
            assert filtered.json()["results"]
            assert filtered.json()["results"][0]["project"] == "fixture-project"
            assert "platform:wsl2" in filtered.json()["results"][0]["tags"]
            excluded = TestClient(app).post(
                "/api/v1/search/hybrid",
                json={
                    "query": "PostgreSQL",
                    "mode": "keyword",
                    "tags": ["situation:performance"],
                },
            )
            assert excluded.status_code == 200
            assert excluded.json()["results"] == []
            facets = TestClient(app).get("/api/v1/search/facets")
            assert facets.status_code == 200
            assert {"name": "fixture-project", "count": 1} in facets.json()["projects"]
            assert {"name": "platform:wsl2", "count": 1} in facets.json()["tags"]
        finally:
            app.dependency_overrides.clear()
        assert scan_root(session, root, settings.max_file_bytes).queued == 0
        session.commit()
        assert (
            session.scalar(select(func.count()).select_from(DocumentVersion))
            == initial_versions + 1
        )
