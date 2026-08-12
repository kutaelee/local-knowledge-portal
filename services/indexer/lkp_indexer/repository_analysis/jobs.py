from __future__ import annotations

from pathlib import Path

from lkp.models import IngestJob, SourceRoot
from sqlalchemy.orm import Session

from ..queue import fail, finish
from ..worker import heartbeat
from .checkpoint import RepositoryAnalysisCheckpointStore
from .pipeline import RepositoryAnalysisPipeline
from .provider import LocalModelProvider
from .repository import RepositoryAnalysisStore


def process_repository_analysis_job(
    session: Session,
    job: IngestJob,
    worker_id: str,
) -> None:
    """Analyze one allowlisted, read-only repository directory."""

    try:
        root = session.get(SourceRoot, job.source_root_id)
        if root is None or not root.enabled:
            raise RuntimeError("source root missing or disabled")
        if not root.read_only:
            raise RuntimeError("repository analysis requires a read-only source root")

        root_path = Path(root.canonical_path).resolve(strict=True)
        source_path = Path(job.canonical_path).resolve(strict=True)
        if not source_path.is_relative_to(root_path):
            raise RuntimeError("repository path is outside the selected source root")
        if not source_path.is_dir():
            raise RuntimeError("repository analysis path must be a directory")

        provider = LocalModelProvider.from_environment()
        with Session(bind=session.get_bind()) as checkpoint_session:
            checkpoint_store = (
                RepositoryAnalysisCheckpointStore(checkpoint_session)
                if provider is not None
                else None
            )
            manifest = RepositoryAnalysisPipeline(
                allowed_roots=[root_path],
                provider=provider,
                checkpoint_store=checkpoint_store,
            ).run(source_path)
        store = RepositoryAnalysisStore(session)
        persisted = store.persist(
            manifest,
            category=str((job.error_details or {}).get("category") or "Library")[:100],
        )
        if provider is not None and (persisted or store.has_analysis(manifest)):
            with Session(bind=session.get_bind()) as checkpoint_session:
                RepositoryAnalysisCheckpointStore(
                    checkpoint_session
                ).mark_promoted(
                    manifest,
                    fingerprint=str(
                        manifest.metrics["analysis_checkpoint_fingerprint"]
                    ),
                )
        job.error_details = {
            **(job.error_details or {}),
            "correlation_id": str(manifest.correlation_id),
            "project_id": str(manifest.project_id),
            "snapshot_id": str(manifest.snapshot_id),
            "persisted": persisted,
            "metrics": manifest.metrics,
            "warnings": manifest.warnings,
            "source_text_stored": False,
            "local_model_used": bool(manifest.metrics.get("llm_requests")),
            "operator_intervention_required": bool(
                manifest.metrics.get("operator_intervention_required")
            ),
        }
        finish(session, job)
        heartbeat(session, worker_id, "idle", success=True)
    except Exception as exc:
        fail(session, job, exc)
        heartbeat(session, worker_id, "error", failure=True)
        raise
