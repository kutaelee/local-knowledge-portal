import json
import shutil
import uuid
from pathlib import Path

from lkp.db import SessionLocal
from lkp.models import SourceRoot
from lkp.schemas import SearchRequest
from lkp.search import search
from lkp.settings import get_settings
from lkp_indexer.embedding import DeterministicTestEmbedder
from lkp_indexer.queue import lease
from lkp_indexer.scanner import scan_root
from lkp_indexer.worker import process_job
from sqlalchemy import select


def main() -> None:
    settings = get_settings().model_copy(
        update={
            "embedding_provider": "deterministic-test",
            "embedding_revision": "validation-deterministic-d1024-v1",
            "embedding_model": "sha256-prng",
            "embedding_model_digest": "test-v1",
        }
    )
    repository = Path(__file__).resolve().parents[1]
    fixture = repository / "tests" / "fixtures" / "sample" / "README.md"
    validation_root = settings.ingest_dir / "validation-source"
    validation_root.mkdir(parents=True, exist_ok=True)
    destination = validation_root / "README.md"
    if not destination.exists():
        shutil.copyfile(fixture, destination)
    with SessionLocal() as session:
        root = session.scalar(
            select(SourceRoot).where(SourceRoot.canonical_path == str(validation_root.resolve()))
        )
        if root is None:
            root = SourceRoot(
                name="validation-fixture",
                canonical_path=str(validation_root.resolve()),
                source_type="validation",
                read_only=True,
                enabled=True,
                include_patterns=["**/*"],
                exclude_patterns=[],
            )
            session.add(root)
            session.commit()
        stats = scan_root(session, root, settings.max_file_bytes)
        session.commit()
        job = lease(session, f"validation-{uuid.uuid4().hex[:8]}", settings.lease_seconds)
        if job:
            process_job(
                session,
                job,
                settings,
                DeterministicTestEmbedder(settings.embedding_dimension),
                "validation-worker",
            )
            session.commit()
        response = search(
            session,
            SearchRequest(query="PostgreSQL row locking", mode="keyword", top_k=5),
            settings,
        )
        session.commit()
        print(
            json.dumps(
                {
                    "scan": {
                        "visited": stats.visited,
                        "queued": stats.queued,
                        "ignored": stats.ignored,
                    },
                    "processed_job": str(job.id) if job else None,
                    "search_results": len(response.results),
                    "provenance": (
                        response.results[0].provenance.model_dump(mode="json")
                        if response.results
                        else None
                    ),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
