import argparse
import socket
import uuid

from lkp.db import SessionLocal
from lkp.settings import get_settings

from .embedding import DeterministicTestEmbedder, OllamaEmbedder
from .queue import lease
from .scanner import register_roots, scan_root
from .worker import process_job


def get_embedder(settings, deterministic: bool):
    if deterministic:
        return DeterministicTestEmbedder(settings.embedding_dimension)
    return OllamaEmbedder(
        settings.ollama_base_url,
        settings.embedding_model,
        settings.embedding_model_digest,
        settings.embedding_dimension,
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="lkp-indexer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan")
    work = sub.add_parser("work-once")
    work.add_argument("--deterministic-test-embedding", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    if args.command == "scan":
        with SessionLocal() as session:
            roots = register_roots(session, settings.source_roots_config)
            for root in roots:
                stats = scan_root(session, root, settings.max_file_bytes)
                print(f"{root.name}: {stats}")
            session.commit()
        return 0
    worker_id = f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as session:
        job = lease(session, worker_id, settings.lease_seconds)
        if not job:
            print("queue empty")
            return 0
        embedder = get_embedder(settings, args.deterministic_test_embedding)
        try:
            process_job(session, job, settings, embedder, worker_id)
        except Exception:
            session.commit()
            raise
        session.commit()
        print(f"processed {job.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
