from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .domain import (
    AnalysisManifest,
    Claim,
    Confidence,
    EvidenceReference,
    ValidationStatus,
)

_CHECKPOINT_NAMESPACE = uuid.UUID("b8cc87ab-c508-4b9e-83f4-8abcf1fa7167")
_TERMINAL_TASK_STATUSES = {
    "SOURCE_EXTRACTED",
    "ADDITIONAL_ANALYSIS_REQUIRED",
}


def analysis_fingerprint(
    manifest: AnalysisManifest,
    *,
    max_claims: int,
    max_output: int | None,
    max_source_chars: int | None,
) -> str:
    payload = {
        "analysis_version": manifest.analysis_version,
        "model": manifest.model,
        "model_quantization": manifest.model_quantization,
        "prompt_version": manifest.prompt_version,
        "max_claims": max_claims,
        "max_output": max_output,
        "max_source_chars": max_source_chars,
        "checkpoint_schema": 1,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def checkpoint_id(snapshot_id: uuid.UUID, fingerprint: str) -> uuid.UUID:
    return uuid.uuid5(_CHECKPOINT_NAMESPACE, f"{snapshot_id}:{fingerprint}")


def _json(value: Any) -> str:
    def default(item: Any) -> str:
        return item.value if hasattr(item, "value") else str(item)

    return json.dumps(value, default=default, ensure_ascii=False)


def _claim_payload(claim: Claim) -> dict[str, Any]:
    return {
        "claim": claim.claim,
        "claim_type": claim.claim_type,
        "component": claim.component,
        "evidence": [asdict(item) for item in claim.evidence],
        "related_configs": claim.related_configs,
        "assumptions": claim.assumptions,
        "unknowns": claim.unknowns,
        "counter_evidence": claim.counter_evidence,
        "confidence": claim.confidence.value,
    }


def _claim_from_payload(payload: dict[str, Any]) -> Claim:
    return Claim(
        claim=str(payload["claim"]),
        claim_type=str(payload["claim_type"]),
        component=str(payload["component"]),
        evidence=[
            EvidenceReference(
                file=str(item["file"]),
                source_hash=str(item["source_hash"]),
                start_line=int(item["start_line"]),
                end_line=int(item["end_line"]),
                symbol=str(item["symbol"]) if item.get("symbol") else None,
            )
            for item in payload.get("evidence", [])
        ],
        related_configs=list(payload.get("related_configs", [])),
        assumptions=[str(item) for item in payload.get("assumptions", [])],
        unknowns=[str(item) for item in payload.get("unknowns", [])],
        counter_evidence=[str(item) for item in payload.get("counter_evidence", [])],
        confidence=Confidence(str(payload.get("confidence", "LOW"))),
        validation_status=ValidationStatus.ADDITIONAL_DATA_NEEDED,
        validation_errors=[],
    )


class RepositoryAnalysisCheckpointStore:
    """Durable task staging that never becomes searchable by itself."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def begin(
        self,
        manifest: AnalysisManifest,
        *,
        fingerprint: str,
        planned_task_count: int,
    ) -> uuid.UUID:
        now = datetime.now(timezone.utc)
        value = checkpoint_id(manifest.snapshot_id, fingerprint)
        self.session.execute(
            text(
                """
                INSERT INTO repository_analysis_checkpoint (
                  id, project_id, snapshot_id, source_hash,
                  analysis_fingerprint, analysis_version, model,
                  model_quantization, prompt_version, planned_task_count,
                  status, metrics, created_at, updated_at, completed_at
                ) VALUES (
                  :id, :project_id, :snapshot_id, :source_hash,
                  :fingerprint, :analysis_version, :model,
                  :model_quantization, :prompt_version, :planned_task_count,
                  'RUNNING', '{}'::jsonb, :now, :now, NULL
                )
                ON CONFLICT (id) DO UPDATE SET
                  status = 'RUNNING',
                  planned_task_count = EXCLUDED.planned_task_count,
                  updated_at = EXCLUDED.updated_at,
                  completed_at = NULL
                """
            ),
            {
                "id": value,
                "project_id": manifest.project_id,
                "snapshot_id": manifest.snapshot_id,
                "source_hash": manifest.source_hash,
                "fingerprint": fingerprint,
                "analysis_version": manifest.analysis_version,
                "model": manifest.model,
                "model_quantization": manifest.model_quantization,
                "prompt_version": manifest.prompt_version,
                "planned_task_count": planned_task_count,
                "now": now,
            },
        )
        self.session.commit()
        return value

    def restore(
        self,
        checkpoint: uuid.UUID,
        tasks: list[dict[str, Any]],
        *,
        retry_exhausted: bool = False,
    ) -> dict[str, tuple[dict[str, Any], list[Claim]]]:
        planned = {str(item["task_id"]): item for item in tasks}
        rows = self.session.execute(
            text(
                """
                SELECT task_id, analysis_unit, status, outcome, claims
                FROM repository_analysis_task_checkpoint
                WHERE checkpoint_id = :checkpoint_id
                """
            ),
            {"checkpoint_id": checkpoint},
        ).mappings()
        restored: dict[str, tuple[dict[str, Any], list[Claim]]] = {}
        for row in rows:
            task_id = str(row["task_id"])
            task = planned.get(task_id)
            if task is None or row["status"] not in _TERMINAL_TASK_STATUSES:
                continue
            outcome = dict(row["outcome"])
            if retry_exhausted and outcome.get("request_failures_exhausted"):
                continue
            for field in ("started_at", "finished_at"):
                value = outcome.get(field)
                if isinstance(value, str):
                    outcome[field] = datetime.fromisoformat(value)
            if str(row["analysis_unit"]) != str(task["key"]) or sorted(
                outcome.get("source_files", [])
            ) != sorted(task.get("files", [])):
                continue
            restored[task_id] = (
                outcome,
                [_claim_from_payload(dict(item)) for item in list(row["claims"])],
            )
        return restored

    def save_task(
        self,
        checkpoint: uuid.UUID,
        outcome: dict[str, Any],
        claims: list[Claim],
    ) -> None:
        status = str(outcome["status"])
        if status not in _TERMINAL_TASK_STATUSES:
            raise ValueError(f"cannot checkpoint non-terminal task: {status}")
        completed_at = outcome.get("finished_at") or datetime.now(timezone.utc)
        self.session.execute(
            text(
                """
                INSERT INTO repository_analysis_task_checkpoint (
                  checkpoint_id, task_id, analysis_unit, status,
                  outcome, claims, completed_at
                ) VALUES (
                  :checkpoint_id, :task_id, :analysis_unit, :status,
                  CAST(:outcome AS jsonb), CAST(:claims AS jsonb), :completed_at
                )
                ON CONFLICT (checkpoint_id, task_id) DO UPDATE SET
                  analysis_unit = EXCLUDED.analysis_unit,
                  status = EXCLUDED.status,
                  outcome = EXCLUDED.outcome,
                  claims = EXCLUDED.claims,
                  completed_at = EXCLUDED.completed_at
                """
            ),
            {
                "checkpoint_id": checkpoint,
                "task_id": uuid.UUID(str(outcome["task_id"])),
                "analysis_unit": str(outcome["key"]),
                "status": status,
                "outcome": _json(outcome),
                "claims": _json([_claim_payload(item) for item in claims]),
                "completed_at": completed_at,
            },
        )
        self.session.execute(
            text(
                """
                UPDATE repository_analysis_checkpoint
                SET updated_at = :updated_at
                WHERE id = :checkpoint_id
                """
            ),
            {
                "updated_at": datetime.now(timezone.utc),
                "checkpoint_id": checkpoint,
            },
        )
        self.session.commit()

    def mark_tasks_complete(
        self,
        checkpoint: uuid.UUID,
        metrics: dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc)
        self.session.execute(
            text(
                """
                UPDATE repository_analysis_checkpoint
                SET status = 'TASKS_COMPLETED',
                    metrics = CAST(:metrics AS jsonb),
                    updated_at = :now,
                    completed_at = :now
                WHERE id = :checkpoint_id
                """
            ),
            {
                "checkpoint_id": checkpoint,
                "metrics": _json(metrics),
                "now": now,
            },
        )
        self.session.commit()

    def mark_promoted(
        self,
        manifest: AnalysisManifest,
        *,
        fingerprint: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        self.session.execute(
            text(
                """
                UPDATE repository_analysis_checkpoint
                SET status = 'PROMOTED',
                    metrics = CAST(:metrics AS jsonb),
                    updated_at = :now,
                    completed_at = :now
                WHERE id = :checkpoint_id
                """
            ),
            {
                "checkpoint_id": checkpoint_id(
                    manifest.snapshot_id,
                    fingerprint,
                ),
                "metrics": _json(manifest.metrics),
                "now": now,
            },
        )
        self.session.commit()
