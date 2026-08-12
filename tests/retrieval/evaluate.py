import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path

from lkp.models import (
    Document,
    DocumentState,
    IngestJob,
    JobStatus,
    SourceRoot,
)
from lkp.schemas import SearchRequest
from lkp.search import search
from lkp.settings import Settings
from lkp_indexer.embedding import OllamaEmbedder
from lkp_indexer.scanner import scan_root
from lkp_indexer.worker import process_job
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class Case:
    kind: str
    query: str
    relevant: frozenset[str]


CASES = [
    Case("exact filename", "queue-adr.md", frozenset({"queue-adr.md"})),
    Case("partial path", "operations", frozenset({"operations.md"})),
    Case("Korean natural language", "원본 경로와 줄 번호 출처", frozenset({"한국어-운영.md"})),
    Case("code symbol", "lease_job_with_skip_locked", frozenset({"symbols.py"})),
    Case("error phrase", '"worker lease expired before completion"', frozenset({"operations.md"})),
    Case("work reason", "왜 별도 메시지 브로커를 쓰지 않았나", frozenset({"queue-adr.md"})),
    Case("ADR decision", "PostgreSQL durable queue", frozenset({"queue-adr.md"})),
    Case("similar expression", "database queue without Kafka", frozenset({"queue-adr.md"})),
    Case("change time", "PostgreSQL 재시작 후 이벤트 복구", frozenset({"operations.md"})),
]


def ingest(session: Session, root: SourceRoot, settings: Settings) -> None:
    scan_root(session, root, settings.max_file_bytes)
    session.commit()
    embedder = OllamaEmbedder(
        settings.ollama_base_url,
        settings.embedding_model,
        settings.embedding_model_digest,
        settings.embedding_dimension,
    )
    jobs = session.scalars(
        select(IngestJob).where(
            IngestJob.source_root_id == root.id,
            IngestJob.status == JobStatus.pending,
        )
    ).all()
    for job in jobs:
        job.leased_by = "retrieval-evaluation"
        job.status = JobStatus.leased
        job.attempt_count += 1
        process_job(
            session,
            job,
            settings,
            embedder,
            f"retrieval-{socket.gethostname()}",
        )
        session.commit()


def evaluate_mode(
    session: Session,
    settings: Settings,
    root: SourceRoot,
    mode: str,
    embedder: OllamaEmbedder,
) -> dict:
    hits5 = hits10 = 0
    reciprocal_ranks = []
    citations = citations_correct = 0
    filtered_correct = True
    details = []
    for case in CASES:
        response = search(
            session,
            SearchRequest(
                query=case.query,
                mode=mode,
                top_k=10,
                source_root_id=root.id,
                minimum_similarity=0.2,
            ),
            settings,
            embedder if mode in {"semantic", "hybrid"} else None,
        )
        ranks = [
            index
            for index, result in enumerate(response.results, 1)
            if result.title in case.relevant
        ]
        first_rank = ranks[0] if ranks else None
        hits5 += int(first_rank is not None and first_rank <= 5)
        hits10 += int(first_rank is not None and first_rank <= 10)
        reciprocal_ranks.append(1 / first_rank if first_rank else 0)
        for result in response.results:
            citations += 1
            provenance = result.provenance
            correct = (
                provenance.start_line > 0
                and provenance.end_line >= provenance.start_line
                and len(provenance.content_hash) == 64
                and Path(provenance.canonical_path).is_relative_to(Path(root.canonical_path))
            )
            citations_correct += int(correct)
            filtered_correct = filtered_correct and correct
        details.append(
            {
                "kind": case.kind,
                "query": case.query,
                "first_relevant_rank": first_rank,
                "results": [item.title for item in response.results[:5]],
            }
        )
    denominator = len(CASES)
    return {
        "mode": mode,
        "hit_rate_at_5": hits5 / denominator,
        "hit_rate_at_10": hits10 / denominator,
        "mrr": sum(reciprocal_ranks) / denominator,
        "filtered_search_correctness": filtered_correct,
        "citation_correctness": citations_correct / citations if citations else 1,
        "cases": details,
    }


def main() -> None:
    database_url = os.getenv("LKP_TEST_DATABASE_URL")
    if not database_url:
        raise SystemExit("LKP_TEST_DATABASE_URL is required; evaluation never uses production DB")
    settings = Settings(database_url=database_url)
    engine = create_engine(database_url)
    fixture = Path("tests/fixtures/retrieval").resolve()
    with Session(engine) as session:
        root = session.scalar(select(SourceRoot).where(SourceRoot.canonical_path == str(fixture)))
        if root is None:
            root = SourceRoot(
                name="retrieval-evaluation",
                canonical_path=str(fixture),
                source_type="validation",
                data_scope="production",
                read_only=True,
                enabled=True,
                include_patterns=["**/*"],
                exclude_patterns=[],
            )
            session.add(root)
            session.commit()
        ingest(session, root, settings)
        embedder = OllamaEmbedder(
            settings.ollama_base_url,
            settings.embedding_model,
            settings.embedding_model_digest,
            settings.embedding_dimension,
        )
        modes = [
            evaluate_mode(session, settings, root, mode, embedder)
            for mode in ("keyword", "semantic", "hybrid")
        ]
        no_answer = search(
            session,
            SearchRequest(
                query="Mars telemetry retention policy for orbital relays",
                mode="semantic",
                top_k=10,
                source_root_id=root.id,
                minimum_similarity=0.8,
            ),
            settings,
            embedder,
        )
        deleted = session.scalar(
            select(Document).where(
                Document.source_root_id == root.id,
                Document.filename == "operations.md",
            )
        )
        deleted.state = DocumentState.deleted
        session.flush()
        stale = search(
            session,
            SearchRequest(
                query='"worker lease expired before completion"',
                mode="keyword",
                top_k=10,
                source_root_id=root.id,
            ),
            settings,
        )
        deleted.state = DocumentState.active
        session.commit()
    print(
        json.dumps(
            {
                "corpus_documents": 4,
                "database_scope": "dedicated-test-database",
                "embedding_revision": settings.embedding_revision,
                "modes": modes,
                "no_answer_correctness": len(no_answer.results) == 0,
                "stale_document_exclusion": all(
                    item.title != "operations.md" for item in stale.results
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
