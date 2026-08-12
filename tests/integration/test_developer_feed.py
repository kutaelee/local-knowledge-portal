import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.config import Config
from lkp.models import (
    ActivityEvent,
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentState,
    DocumentVersion,
    ProjectJournalEntry,
    SourceRoot,
)
from lkp.settings import Settings
from lkp_indexer.developer_feed import (
    _eligible_batch,
    _embedded_documents,
    _journal_document_filename,
    publish_once,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


class _RejectingFeedProvider:
    provider = "synthetic-local"
    model = "synthetic-feed-model"

    def write_developer_feed(self, payload, *, prompt_version):
        raise ValueError("synthetic deterministic content rejection")


@pytest.fixture
def database_url():
    value = os.getenv("LKP_TEST_DATABASE_URL")
    if not value:
        pytest.skip("LKP_TEST_DATABASE_URL is not set")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", value)
    command.upgrade(config, "head")
    return value


def test_journal_matches_its_embedded_generated_page_not_changed_source_basename(
    database_url: str,
    tmp_path,
):
    engine = create_engine(database_url)
    now = datetime.now(timezone.utc)
    project = f"feed-fixture-{uuid.uuid4().hex[:8]}"
    settings = Settings(
        database_url=database_url,
        embedding_revision="feed-test-d1024-v1",
        embedding_dimension=1024,
        developer_feed_enabled=True,
        developer_feed_daily_hour=23,
    )
    with Session(engine) as session:
        root = SourceRoot(
            name=project,
            canonical_path=str(tmp_path),
            source_type="managed-vault",
            read_only=False,
            enabled=True,
            include_patterns=["**/*.md"],
            exclude_patterns=[],
        )
        session.add(root)
        session.flush()
        stop = ActivityEvent(
            event_key=f"stop:{uuid.uuid4()}",
            session_id="feed-test-session",
            event_type="stop",
            occurred_at=now - timedelta(minutes=10),
            project_key=project,
            changed_files=["src/feature.py"],
            verification_status="VERIFIED",
            metadata_json={},
        )
        session.add(stop)
        session.flush()
        journal = ProjectJournalEntry(
            source_stop_activity_id=stop.id,
            project_key=project,
            occurred_at=stop.occurred_at,
            title="Generated page identity",
            intent="Match a journal to its generated page.",
            change_summary="The changed source basename differs from the generated page.",
            failures_json=[],
            resolution="Use the journal timestamp and ID.",
            verification_json=[{"exit_code": 0, "verified": True}],
            changed_files=["src/feature.py"],
            knowledge_references_json=[],
            significance_reasons=["regression"],
            verification_status="VERIFIED",
            metadata_json={},
        )
        session.add(journal)
        session.flush()
        filename = _journal_document_filename(journal)
        document = Document(
            source_root_id=root.id,
            canonical_path=str(tmp_path / filename),
            relative_path=f"_generated/Projects/{project}/Journal/{filename}",
            filename=filename,
            extension=".md",
            mime_type="text/markdown",
            project_key=project,
            project_relative_path=f"Journal/{filename}",
            parent_path=f"_generated/Projects/{project}/Journal",
            size_bytes=128,
            modified_at_fs=now,
            current_content_hash="a" * 64,
            state=DocumentState.active,
        )
        session.add(document)
        session.flush()
        version = DocumentVersion(
            document_id=document.id,
            content_hash="a" * 64,
            byte_size=128,
            modified_at_fs=now,
            parser_version="test-parser",
            chunker_version="test-chunker",
            line_count=3,
            metadata_json={"project": project},
            change_type="created",
            diff_summary={},
        )
        session.add(version)
        session.flush()
        document.current_version_id = version.id
        chunk = DocumentChunk(
            document_version_id=version.id,
            chunk_index=0,
            chunk_type="section",
            heading_path="Verification",
            start_line=1,
            end_line=3,
            content="Synthetic verified feed evidence.",
            content_hash="b" * 64,
            token_estimate=8,
            metadata_json={},
        )
        session.add(chunk)
        session.flush()
        session.add(
            ChunkEmbedding(
                chunk_id=chunk.id,
                embedding_revision=settings.embedding_revision,
                provider="deterministic-test",
                model="deterministic-test",
                model_digest="sha256:deterministic-test",
                dimension=1024,
                embedding=[0.0] * 1024,
            )
        )
        session.flush()

        matched = _embedded_documents(session, journal, settings)

        assert len(matched) == 1
        assert matched[0]["document_id"] == str(document.id)
        assert matched[0]["filename"] == filename

        assert (
            _eligible_batch(
                session,
                settings,
                now=now,
                excluded_projects={project},
            )
            is None
        )
        rejected = publish_once(
            session,
            settings,
            now=now,
            provider=_RejectingFeedProvider(),
        )
        assert rejected["state"] == "rejected_content"
        assert rejected["content_rejections"] == [
            {
                "post_type": "activity",
                "project": project,
                "reason": "deterministic_content_gate_rejected",
            }
        ]
        session.rollback()
