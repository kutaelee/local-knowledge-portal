from __future__ import annotations

import argparse
import json
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from .checkpoint import RepositoryAnalysisCheckpointStore
from .pipeline import RepositoryAnalysisPipeline
from .provider import LocalModelProvider
from .repository import RepositoryAnalysisStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze an allowlisted repository without storing source text."
    )
    parser.add_argument("source_root")
    parser.add_argument("--allowed-root", action="append", default=[])
    parser.add_argument("--database-url", default=os.getenv("LKP_DATABASE_URL"))
    parser.add_argument("--category", default="Library")
    parser.add_argument("--enable-local-model", action="store_true")
    parser.add_argument("--max-claims", type=int, default=500)
    parser.add_argument(
        "--evidence-root",
        action="append",
        default=[],
        metavar="PREFIX=PATH",
        help="Additional read-only derived evidence root, such as decompiled JARs.",
    )
    args = parser.parse_args()

    evidence_roots: dict[str, str] = {}
    for value in args.evidence_root:
        prefix, separator, path = value.partition("=")
        if not separator or not prefix or not path:
            parser.error("--evidence-root must use PREFIX=PATH")
        evidence_roots[prefix] = path
    provider = LocalModelProvider.from_environment() if args.enable_local_model else None
    persisted = None
    if args.database_url:
        engine = create_engine(args.database_url)
        with Session(engine) as session:
            checkpoint_store = (
                RepositoryAnalysisCheckpointStore(session)
                if provider is not None
                else None
            )
            manifest = RepositoryAnalysisPipeline(
                allowed_roots=args.allowed_root or [args.source_root],
                provider=provider,
                max_claims=args.max_claims,
                evidence_roots=evidence_roots,
                checkpoint_store=checkpoint_store,
            ).run(args.source_root)
            persisted = RepositoryAnalysisStore(session).persist(
                manifest,
                category=args.category,
            )
            if checkpoint_store is not None:
                checkpoint_store.mark_promoted(
                    manifest,
                    fingerprint=str(
                        manifest.metrics["analysis_checkpoint_fingerprint"]
                    ),
                )
    else:
        manifest = RepositoryAnalysisPipeline(
            allowed_roots=args.allowed_root or [args.source_root],
            provider=provider,
            max_claims=args.max_claims,
            evidence_roots=evidence_roots,
        ).run(args.source_root)
    print(
        json.dumps(
            {
                "project_id": str(manifest.project_id),
                "snapshot_id": str(manifest.snapshot_id),
                "stage": manifest.stage.value,
                "source_hash": manifest.source_hash,
                "metrics": manifest.metrics,
                "warnings": manifest.warnings,
                "persisted": persisted,
                "source_text_stored": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
