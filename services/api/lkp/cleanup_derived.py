import argparse
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import (
    ActivityEvent,
    ChunkEmbedding,
    Document,
    DocumentChunk,
    DocumentLink,
    DocumentState,
    DocumentTag,
    DocumentVersion,
    EvidenceRecord,
    HookSpoolEvent,
    IngestJob,
    JobStatus,
)

ACTIVE_JOB_STATES = {
    JobStatus.pending,
    JobStatus.leased,
    JobStatus.processing,
}
VALIDATION_SESSION_IDS = {"wsl-transition-validation"}


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def cleanup_ignored(
    *,
    apply: bool,
    manifest_path: Path | None = None,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, Any]:
    with session_factory() as session:
        documents = session.scalars(
            select(Document)
            .where(Document.state == DocumentState.ignored)
            .order_by(Document.canonical_path)
            .with_for_update()
        ).all()
        document_ids = [row.id for row in documents]
        version_ids = list(
            session.scalars(
                select(DocumentVersion.id).where(
                    DocumentVersion.document_id.in_(document_ids)
                )
            )
        ) if document_ids else []
        chunk_ids = list(
            session.scalars(
                select(DocumentChunk.id).where(
                    DocumentChunk.document_version_id.in_(version_ids)
                )
            )
        ) if version_ids else []
        active_jobs = int(
            session.scalar(
                select(func.count())
                .select_from(IngestJob)
                .where(
                    IngestJob.document_id.in_(document_ids),
                    IngestJob.status.in_(ACTIVE_JOB_STATES),
                )
            )
            or 0
        ) if document_ids else 0
        if active_jobs:
            raise RuntimeError(
                f"refusing cleanup while {active_jobs} target jobs are active"
            )

        result: dict[str, Any] = {
            "policy": "delete-derived-documents-in-ignored-state-v1",
            "status": "planned",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_files_deleted": 0,
            "documents": len(document_ids),
            "versions": len(version_ids),
            "chunks": len(chunk_ids),
            "embeddings": int(
                session.scalar(
                    select(func.count())
                    .select_from(ChunkEmbedding)
                    .where(ChunkEmbedding.chunk_id.in_(chunk_ids))
                )
                or 0
            ) if chunk_ids else 0,
            "paths": [row.canonical_path for row in documents],
        }
        if not apply:
            return result

        if chunk_ids:
            session.execute(
                delete(ChunkEmbedding).where(ChunkEmbedding.chunk_id.in_(chunk_ids))
            )
            session.execute(
                delete(DocumentChunk).where(DocumentChunk.id.in_(chunk_ids))
            )
        if version_ids:
            session.execute(
                delete(DocumentVersion).where(DocumentVersion.id.in_(version_ids))
            )
        if document_ids:
            session.execute(
                delete(DocumentLink).where(
                    or_(
                        DocumentLink.source_document_id.in_(document_ids),
                        DocumentLink.target_document_id.in_(document_ids),
                    )
                )
            )
            session.execute(
                delete(DocumentTag).where(DocumentTag.document_id.in_(document_ids))
            )
            session.execute(
                update(IngestJob)
                .where(IngestJob.document_id.in_(document_ids))
                .values(document_id=None)
            )
            session.execute(delete(Document).where(Document.id.in_(document_ids)))
        session.commit()
        result["status"] = "completed"
        result["completed_at"] = datetime.now(timezone.utc).isoformat()

    if manifest_path is not None:
        _atomic_json(manifest_path, result)
    return result


def cleanup_validation_activity(
    *,
    apply: bool,
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, Any]:
    with session_factory() as session:
        activities = session.scalars(
            select(ActivityEvent).where(
                ActivityEvent.session_id.in_(VALIDATION_SESSION_IDS)
            )
        ).all()
        activity_ids = [row.id for row in activities]
        evidence_count = int(
            session.scalar(
                select(func.count())
                .select_from(EvidenceRecord)
                .where(EvidenceRecord.activity_id.in_(activity_ids))
            )
            or 0
        ) if activity_ids else 0
        if evidence_count:
            raise RuntimeError(
                f"refusing cleanup: {evidence_count} evidence rows reference fixtures"
            )
        spool_events = session.scalars(
            select(HookSpoolEvent).where(
                HookSpoolEvent.session_id.in_(VALIDATION_SESSION_IDS)
            )
        ).all()
        result: dict[str, Any] = {
            "policy": "delete-explicit-validation-activity-v1",
            "status": "planned",
            "sessions": sorted(VALIDATION_SESSION_IDS),
            "activities": len(activity_ids),
            "spool_events": len(spool_events),
            "raw_spool_files": 0,
        }
        if not apply:
            return result
        if activity_ids:
            session.execute(
                delete(ActivityEvent).where(ActivityEvent.id.in_(activity_ids))
            )
        event_ids = [row.event_id for row in spool_events]
        if event_ids:
            session.execute(
                delete(HookSpoolEvent).where(HookSpoolEvent.event_id.in_(event_ids))
            )
        session.commit()

    spool_root = Path("/data/ingest/codex-spool")
    deleted_files = 0
    for event_id in event_ids:
        for directory in ("pending", "processing", "processed", "quarantine"):
            candidate = spool_root / directory / f"{event_id}.json"
            if candidate.is_file():
                candidate.unlink()
                deleted_files += 1
    result["raw_spool_files"] = deleted_files
    result["status"] = "completed"
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete rebuildable DB rows for documents already classified as ignored."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--include-validation-fixtures", action="store_true")
    args = parser.parse_args()
    result: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ignored_documents": cleanup_ignored(apply=args.apply),
    }
    if args.include_validation_fixtures:
        result["validation_activity"] = cleanup_validation_activity(apply=args.apply)
    result["status"] = "completed" if args.apply else "planned"
    if args.manifest is not None and args.apply:
        _atomic_json(args.manifest, result)
    print(
        json.dumps(
            result,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
