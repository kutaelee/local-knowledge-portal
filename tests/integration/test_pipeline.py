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
    DocumentVersion,
    SourceRoot,
)
from lkp.settings import Settings
from lkp_indexer.embedding import DeterministicTestEmbedder
from lkp_indexer.queue import lease
from lkp_indexer.scanner import scan_root
from lkp_indexer.worker import process_job
from sqlalchemy import create_engine, func, select
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
    copied.write_bytes(fixture.read_bytes())
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
        finally:
            app.dependency_overrides.clear()
        assert scan_root(session, root, settings.max_file_bytes).queued == 0
        session.commit()
        assert (
            session.scalar(select(func.count()).select_from(DocumentVersion))
            == initial_versions + 1
        )
