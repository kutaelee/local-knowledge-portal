from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path

from lkp.db import SessionLocal
from lkp.models import Document
from lkp.settings import get_settings

from .queue import enqueue
from .service_runtime import assert_mount_guards

_RESYNC_REVISION = "managed-frontmatter-metadata-v1"


def _key(document: Document) -> str:
    payload = "\0".join(
        (
            _RESYNC_REVISION,
            str(document.source_root_id),
            document.canonical_path,
            document.current_content_hash or "",
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Queue a non-destructive metadata resync for managed generated documents."
    )
    parser.add_argument("--document-id", action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    assert_mount_guards(settings)
    managed_root = (settings.vault_dir / "_generated").resolve()
    results: list[dict] = []
    with SessionLocal() as session:
        for raw_id in args.document_id:
            document_id = uuid.UUID(raw_id)
            document = session.get(Document, document_id)
            if document is None:
                results.append(
                    {
                        "document_id": str(document_id),
                        "status": "skipped",
                        "reason": "document_missing",
                    }
                )
                continue
            canonical = Path(document.canonical_path).resolve()
            if not canonical.is_relative_to(managed_root):
                results.append(
                    {
                        "document_id": str(document_id),
                        "status": "skipped",
                        "reason": "outside_managed_directory",
                    }
                )
                continue
            result = {
                "document_id": str(document.id),
                "canonical_path": str(canonical),
                "status": "would_queue",
            }
            if args.apply:
                job = enqueue(
                    session,
                    key=_key(document),
                    source_root_id=document.source_root_id,
                    canonical_path=str(canonical),
                    job_type="metadata_resync",
                    priority=20,
                    details={
                        "reason": _RESYNC_REVISION,
                        "document_id": str(document.id),
                    },
                )
                result["status"] = "queued" if job else "already_queued"
                result["job_id"] = str(job.id) if job else None
            results.append(result)
        if args.apply:
            session.commit()
        else:
            session.rollback()
    print(json.dumps({"applied": args.apply, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
