from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone

from lkp.db import SessionLocal
from lkp.models import KnowledgeCase, KnowledgeCaseRevision
from lkp.settings import get_settings
from sqlalchemy import select

from .case_pages import materialize_case
from .knowledge import case_tags
from .service_runtime import assert_mount_guards


def backfill_case(
    session,
    case: KnowledgeCase,
    *,
    project: str,
    apply: bool,
) -> dict:
    latest = session.scalar(
        select(KnowledgeCaseRevision)
        .where(KnowledgeCaseRevision.case_id == case.id)
        .order_by(KnowledgeCaseRevision.revision_number.desc())
    )
    if latest is None:
        return {"case_id": str(case.id), "status": "skipped", "reason": "revision_missing"}
    content = dict(latest.content_json or {})
    metadata = dict(case.metadata_json or {})
    tags = case_tags(
        category=case.category,
        project=project,
        raw_tags=content.get("tags") or metadata.get("tags"),
        knowledge_value=content.get("knowledge_value"),
    )
    if content.get("project") == project and content.get("tags") == tags:
        return {
            "case_id": str(case.id),
            "status": "unchanged",
            "revision": latest.revision_number,
        }
    result = {
        "case_id": str(case.id),
        "status": "would_update" if not apply else "updated",
        "from_revision": latest.revision_number,
        "to_revision": latest.revision_number + 1,
        "project": project,
        "tags": tags,
    }
    if not apply:
        return result
    now = datetime.now(timezone.utc)
    content.update(
        {
            "project": project,
            "tags": tags,
            "metadata_revision_reason": "project_and_taxonomy_backfill",
            "metadata_revised_at": now.isoformat(),
        }
    )
    evidence_summary = dict(latest.evidence_summary or {})
    evidence_summary["metadata_backfill"] = {
        "reason": "project_and_taxonomy_backfill",
        "source_revision": latest.revision_number,
        "applied_at": now.isoformat(),
    }
    revision = KnowledgeCaseRevision(
        case_id=case.id,
        revision_number=latest.revision_number + 1,
        content_json=content,
        evidence_summary=evidence_summary,
    )
    session.add(revision)
    session.flush()
    case.current_revision_id = revision.id
    metadata.update({"project": project, "tags": tags})
    case.metadata_json = metadata
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--case-id", action="append", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    assert_mount_guards(settings)
    case_ids = [uuid.UUID(value) for value in args.case_id]
    results: list[dict] = []
    with SessionLocal() as session:
        for case_id in case_ids:
            case = session.get(KnowledgeCase, case_id)
            if case is None:
                results.append(
                    {"case_id": str(case_id), "status": "skipped", "reason": "case_missing"}
                )
                continue
            result = backfill_case(
                session,
                case,
                project=args.project.strip(),
                apply=args.apply,
            )
            results.append(result)
            if args.apply and result["status"] == "updated":
                materialize_case(
                    session,
                    case,
                    vault_dir=settings.vault_dir,
                    pipeline_version=settings.pipeline_version,
                    content_language=settings.knowledge_content_language,
                )
        if args.apply:
            session.commit()
        else:
            session.rollback()
    print(json.dumps({"applied": args.apply, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
