import argparse
import socket
import uuid
from pathlib import Path

from lkp.db import SessionLocal
from lkp.settings import get_settings

from .document_link_backfill import backfill_document_links
from .embedding import DeterministicTestEmbedder, OllamaEmbedder, RateLimitedEmbedder
from .project_journal import refresh_journal_presentation
from .queue import lease
from .reconcile import reconcile_root
from .scanner import register_roots, scan_root
from .semantic_policy_migration import migrate_semantic_policy
from .worker import process_job


def get_embedder(settings, deterministic: bool):
    if deterministic:
        return DeterministicTestEmbedder(settings.embedding_dimension)
    return RateLimitedEmbedder(
        OllamaEmbedder(
            settings.ollama_base_url,
            settings.embedding_model,
            settings.embedding_model_digest,
            settings.embedding_dimension,
            settings.embedding_request_timeout_seconds,
            settings.embedding_keep_alive,
        ),
        settings.embedding_batch_size,
        settings.embedding_batch_cooldown_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="lkp-indexer")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan")
    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--root", help="Limit reconciliation to one root name")
    sub.add_parser("refresh-journal-presentation")
    sub.add_parser("backfill-document-links")
    policy_migration = sub.add_parser("migrate-semantic-policy")
    policy_migration.add_argument("--apply", action="store_true")
    policy_migration.add_argument("--manifest", type=Path)
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
    if args.command == "reconcile":
        with SessionLocal() as session:
            roots = register_roots(session, settings.source_roots_config)
            selected = [
                root
                for root in roots
                if args.root is None or root.name == args.root
            ]
            if args.root and not selected:
                raise ValueError(f"unknown enabled source root: {args.root}")
            for root in selected:
                stats = reconcile_root(session, root, settings)
                print(f"{root.name}: {stats}")
            session.commit()
        return 0
    if args.command == "refresh-journal-presentation":
        with SessionLocal() as session:
            result = refresh_journal_presentation(
                session,
                vault_dir=settings.vault_dir,
                pipeline_version=settings.pipeline_version,
                content_language=settings.knowledge_content_language,
            )
            session.commit()
        print(result)
        return 0
    if args.command == "backfill-document-links":
        with SessionLocal() as session:
            result = backfill_document_links(
                session,
                max_file_bytes=settings.max_file_bytes,
            )
            session.commit()
        print(result)
        return 0
    if args.command == "migrate-semantic-policy":
        result = migrate_semantic_policy(
            apply=args.apply,
            repository_mode=settings.repository_embedding_mode,
            manifest_path=args.manifest,
        )
        print(result)
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
