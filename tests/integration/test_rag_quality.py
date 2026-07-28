import os
import uuid
from datetime import datetime, timezone

import pytest
from alembic import command
from alembic.config import Config
from lkp.models import (
    Document,
    DocumentChunk,
    DocumentState,
    DocumentVersion,
    SourceRoot,
)
from lkp.rag_quality import build_expanded_context
from lkp.schemas import Provenance, SearchResult
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def database_url() -> str:
    value = os.getenv("LKP_TEST_DATABASE_URL")
    if not value or "lkp_test_" not in value:
        pytest.skip("dedicated LKP_TEST_DATABASE_URL is required")
    return value


def test_context_expansion_preserves_chunk_citations_and_excludes_stale_source(
    database_url: str,
) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    engine = create_engine(database_url)
    now = datetime.now(timezone.utc)
    root = SourceRoot(
        id=uuid.uuid4(),
        name=f"rag-{uuid.uuid4()}",
        canonical_path=f"/fixture/{uuid.uuid4()}",
        source_type="validation",
        data_scope="production",
        read_only=True,
        enabled=True,
    )
    document = Document(
        id=uuid.uuid4(),
        source_root_id=root.id,
        canonical_path=f"{root.canonical_path}/runbook.md",
        relative_path="runbook.md",
        filename="runbook.md",
        extension=".md",
        mime_type="text/markdown",
        project_key="fixture-project",
        project_relative_path="runbook.md",
        parent_path="",
        size_bytes=100,
        modified_at_fs=now,
        current_content_hash="d" * 64,
        state=DocumentState.active,
    )
    version = DocumentVersion(
        id=uuid.uuid4(),
        document_id=document.id,
        content_hash="d" * 64,
        byte_size=100,
        modified_at_fs=now,
        parser_version="test",
        chunker_version="test",
        line_count=6,
        change_type="created",
    )
    chunks = [
        DocumentChunk(
            id=uuid.uuid4(),
            document_version_id=version.id,
            chunk_index=index,
            chunk_type="markdown_section",
            heading_path=f"Section {index}",
            symbol_name=None,
            language=None,
            start_line=index * 2 + 1,
            end_line=index * 2 + 2,
            content=content,
            content_hash=str(index + 1) * 64,
            token_estimate=4,
        )
        for index, content in enumerate(
            [
                "Failure symptom and initial observation.",
                "Root cause was an expired database lease.",
                "Renew the lease and rerun the worker.",
            ]
        )
    ]
    with Session(engine) as session:
        session.add(root)
        session.flush()
        session.add(document)
        session.flush()
        session.add(version)
        session.flush()
        session.add_all(chunks)
        session.flush()
        document.current_version_id = version.id
        session.commit()
        anchor = chunks[1]
        result = SearchResult(
            title=document.filename,
            project=document.project_key,
            tags=["case:error_resolution", "lifecycle:verified"],
            heading_or_symbol=anchor.heading_path,
            snippet=anchor.content,
            lexical_rank=0.5,
            vector_similarity=None,
            fused_rank=1 / 61,
            match_reason=["full-text match"],
            provenance=Provenance(
                document_id=document.id,
                document_version_id=version.id,
                chunk_id=anchor.id,
                source_root=root.name,
                canonical_path=document.canonical_path,
                relative_path=document.relative_path,
                start_line=anchor.start_line,
                end_line=anchor.end_line,
                content_hash=anchor.content_hash,
                indexed_timestamp=version.detected_at.isoformat(),
            ),
        )
        contexts = build_expanded_context(session, [result], max_chars=500)
        assert [item["provenance"]["chunk_id"] for item in contexts] == [
            str(chunks[1].id),
            str(chunks[0].id),
            str(chunks[2].id),
        ]
        assert all(len(item["provenance"]["content_hash"]) == 64 for item in contexts)
        assert sum(len(item["content"]) for item in contexts) <= 500

        document.state = DocumentState.deleted
        session.flush()
        assert build_expanded_context(session, [result], max_chars=500) == []
