from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from .domain import AnalysisManifest


def _json(value: Any) -> str:
    def default(item: Any) -> str:
        return item.value if hasattr(item, "value") else str(item)

    return json.dumps(value, default=default, ensure_ascii=False)


class RepositoryAnalysisStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def persist(self, manifest: AnalysisManifest, *, category: str = "Library") -> bool:
        """Persist one immutable snapshot. Returns False when it already exists."""
        now = datetime.now(timezone.utc)
        project = self.session.execute(
            text(
                """
                SELECT id FROM repository_project
                WHERE repository_origin_hash = :origin_hash
                """
            ),
            {"origin_hash": manifest.repository_origin_hash},
        ).scalar_one_or_none()
        if project is None:
            display_name = self._available_display_name(
                manifest.display_name,
                manifest.repository_origin_hash,
                now,
            )
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_project (
                      id, canonical_name, display_name, description, source_type,
                      local_source_reference, repository_origin_hash, category,
                      status, created_at, updated_at
                    ) VALUES (
                      :id, :canonical_name, :display_name, NULL, 'LOCAL_REPOSITORY',
                      :source_reference, :origin_hash, :category,
                      'ACTIVE', :created_at, :updated_at
                    )
                    """
                ),
                {
                    "id": manifest.project_id,
                    "canonical_name": manifest.canonical_name,
                    "display_name": display_name,
                    "source_reference": manifest.source_reference,
                    "origin_hash": manifest.repository_origin_hash,
                    "category": category,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            project = manifest.project_id

        existing = self.session.execute(
            text(
                """
                SELECT id FROM repository_snapshot
                WHERE project_id = :project_id AND source_hash = :source_hash
                """
            ),
            {"project_id": project, "source_hash": manifest.source_hash},
        ).scalar_one_or_none()
        if existing is not None:
            return False

        languages = sorted({item.language for item in manifest.files})
        build_systems = sorted(
            {
                {
                    "MAVEN": "Maven",
                    "NPM": "Node",
                    "PYPI": "Python",
                }.get(item.artifact_type, item.artifact_type)
                for item in manifest.dependencies
            }
        )
        self.session.execute(
            text(
                """
                UPDATE repository_snapshot SET stale = true
                WHERE project_id = :project_id AND stale = false
                """
            ),
            {"project_id": project},
        )
        self.session.execute(
            text(
                """
                INSERT INTO repository_snapshot (
                  id, project_id, snapshot_name, source_hash, git_commit, git_branch,
                  dirty_worktree, analysis_base_time, file_count, languages,
                  build_systems, status, stale, created_at
                ) VALUES (
                  :id, :project_id, :snapshot_name, :source_hash, :git_commit,
                  :git_branch, :dirty_worktree, :analysis_base_time, :file_count,
                  CAST(:languages AS text[]), CAST(:build_systems AS text[]),
                  :status, false, :created_at
                )
                """
            ),
            {
                "id": manifest.snapshot_id,
                "project_id": project,
                "snapshot_name": manifest.snapshot_name,
                "source_hash": manifest.source_hash,
                "git_commit": manifest.git_commit,
                "git_branch": manifest.git_branch,
                "dirty_worktree": manifest.dirty_worktree,
                "analysis_base_time": manifest.started_at,
                "file_count": len(manifest.files),
                "languages": languages,
                "build_systems": build_systems,
                "status": manifest.stage.value,
                "created_at": now,
            },
        )
        self._persist_facts(manifest)
        self._persist_job(manifest, project, now)
        self._persist_evaluation(manifest, project, now)
        self.session.execute(
            text(
                """
                UPDATE repository_project
                SET updated_at = :updated_at, status = 'ACTIVE'
                WHERE id = :project_id
                """
            ),
            {"updated_at": now, "project_id": project},
        )
        self.session.commit()
        return True

    def _available_display_name(
        self,
        base: str,
        origin_hash: str,
        now: datetime,
    ) -> str:
        candidates = [
            base,
            f"{base}-{now:%Y%m%d}",
            f"{base}-{now:%Y%m%d-%H%M}",
            f"{base}-{now:%Y%m%d}-{origin_hash[:8]}",
        ]
        for candidate in candidates:
            exists = self.session.execute(
                text("SELECT 1 FROM repository_project WHERE display_name = :name"),
                {"name": candidate},
            ).first()
            if exists is None:
                return candidate
        return f"{base}-{uuid.uuid4().hex[:8]}"

    def _persist_facts(self, manifest: AnalysisManifest) -> None:
        for item in manifest.files:
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_source_file (
                      id, snapshot_id, relative_path, content_hash, language,
                      module, line_count, size_bytes
                    ) VALUES (
                      :id, :snapshot_id, :relative_path, :content_hash, :language,
                      :module, :line_count, :size_bytes
                    )
                    """
                ),
                {"id": uuid.uuid4(), "snapshot_id": manifest.snapshot_id, **asdict(item)},
            )
        for item in manifest.symbols:
            values = asdict(item)
            values["metadata"] = _json(values["metadata"])
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_source_symbol (
                      id, snapshot_id, relative_path, symbol, symbol_type,
                      start_line, end_line, signature_hash, metadata
                    ) VALUES (
                      :id, :snapshot_id, :relative_path, :symbol, :symbol_type,
                      :start_line, :end_line, :signature_hash, CAST(:metadata AS jsonb)
                    )
                    """
                ),
                {"id": uuid.uuid4(), "snapshot_id": manifest.snapshot_id, **values},
            )
        for item in manifest.relations:
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_source_relation (
                      id, snapshot_id, source_symbol, target_symbol, relation_type,
                      provenance, relative_path, line
                    ) VALUES (
                      :id, :snapshot_id, :source_symbol, :target_symbol,
                      :relation_type, :provenance, :relative_path, :line
                    )
                    """
                ),
                {"id": uuid.uuid4(), "snapshot_id": manifest.snapshot_id, **asdict(item)},
            )
        for item in manifest.configurations:
            values = asdict(item)
            values["referenced_by"] = list(item.referenced_by)
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_configuration_reference (
                      id, snapshot_id, config_key, relative_path, declaration_line,
                      referenced_by, has_default, runtime_value_verified
                    ) VALUES (
                      :id, :snapshot_id, :key, :relative_path, :declaration_line,
                      CAST(:referenced_by AS text[]), :has_default, :runtime_value_verified
                    )
                    """
                ),
                {"id": uuid.uuid4(), "snapshot_id": manifest.snapshot_id, **values},
            )
        for item in manifest.dependencies:
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_dependency_artifact (
                      id, snapshot_id, name, version, artifact_type, classification,
                      relative_path, scope, analysis_status
                    ) VALUES (
                      :id, :snapshot_id, :name, :version, :artifact_type,
                      :classification, :relative_path, :scope, :analysis_status
                    )
                    """
                ),
                {"id": uuid.uuid4(), "snapshot_id": manifest.snapshot_id, **asdict(item)},
            )
        for item in manifest.components:
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_component (
                      id, snapshot_id, component_key, display_name, component_type,
                      responsibility, relative_paths, entry_points, evidence,
                      validation_status, confidence
                    ) VALUES (
                      :id, :snapshot_id, :key, :display_name, :component_type,
                      :responsibility, CAST(:relative_paths AS text[]),
                      CAST(:entry_points AS text[]), CAST(:evidence AS jsonb),
                      :validation_status, :confidence
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "snapshot_id": manifest.snapshot_id,
                    "key": item.key,
                    "display_name": item.display_name,
                    "component_type": item.component_type,
                    "responsibility": item.responsibility,
                    "relative_paths": list(item.relative_paths),
                    "entry_points": list(item.entry_points),
                    "evidence": _json([asdict(value) for value in item.evidence]),
                    "validation_status": item.validation_status.value,
                    "confidence": item.confidence.value,
                },
            )
        for item in manifest.lifecycle_nodes:
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_lifecycle_node (
                      id, snapshot_id, node_key, phase, title, description,
                      component_key, sequence, evidence, validation_status, confidence
                    ) VALUES (
                      :id, :snapshot_id, :key, :phase, :title, :description,
                      :component_key, :sequence, CAST(:evidence AS jsonb),
                      :validation_status, :confidence
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "snapshot_id": manifest.snapshot_id,
                    "key": item.key,
                    "phase": item.phase,
                    "title": item.title,
                    "description": item.description,
                    "component_key": item.component_key,
                    "sequence": item.sequence,
                    "evidence": _json([asdict(value) for value in item.evidence]),
                    "validation_status": item.validation_status.value,
                    "confidence": item.confidence.value,
                },
            )
        for item in manifest.lifecycle_edges:
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_lifecycle_edge (
                      id, snapshot_id, source_key, target_key, relation_type,
                      label, provenance, evidence
                    ) VALUES (
                      :id, :snapshot_id, :source_key, :target_key, :relation_type,
                      :label, :provenance, CAST(:evidence AS jsonb)
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "snapshot_id": manifest.snapshot_id,
                    "source_key": item.source_key,
                    "target_key": item.target_key,
                    "relation_type": item.relation_type,
                    "label": item.label,
                    "provenance": item.provenance,
                    "evidence": _json([asdict(value) for value in item.evidence]),
                },
            )
        for item in manifest.dependency_usages:
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_dependency_usage (
                      id, snapshot_id, dependency_name, component_key, usage_type,
                      relative_path, line, provenance
                    ) VALUES (
                      :id, :snapshot_id, :dependency_name, :component_key, :usage_type,
                      :relative_path, :line, :provenance
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "snapshot_id": manifest.snapshot_id,
                    **asdict(item),
                },
            )

    def _persist_job(
        self,
        manifest: AnalysisManifest,
        project_id: uuid.UUID,
        now: datetime,
    ) -> None:
        job_id = uuid.uuid4()
        self.session.execute(
            text(
                """
                INSERT INTO repository_analysis_job (
                  id, project_id, snapshot_id, correlation_id, stage, status,
                  analysis_version, model, model_quantization, prompt_version,
                  metrics, warnings, error_code, error_message, started_at, finished_at
                ) VALUES (
                  :id, :project_id, :snapshot_id, :correlation_id, :stage, 'SUCCEEDED',
                  :analysis_version, :model, :model_quantization, :prompt_version,
                  CAST(:metrics AS jsonb),
                  CAST(:warnings AS jsonb), NULL, NULL, :started_at, :finished_at
                )
                """
            ),
            {
                "id": job_id,
                "project_id": project_id,
                "snapshot_id": manifest.snapshot_id,
                "correlation_id": manifest.correlation_id,
                "stage": manifest.stage.value,
                "analysis_version": manifest.analysis_version,
                "model": manifest.model,
                "model_quantization": manifest.model_quantization,
                "prompt_version": manifest.prompt_version,
                "metrics": _json(manifest.metrics),
                "warnings": _json(manifest.warnings),
                "started_at": manifest.started_at,
                "finished_at": manifest.completed_at,
            },
        )
        for task in manifest.metrics.get("llm_task_outcomes", []):
            key = str(task.get("key", "unknown"))
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_analysis_task (
                      id, job_id, task_type, seed_symbol, scope, status,
                      attempt_count, failure_code, started_at, finished_at
                    ) VALUES (
                      :id, :job_id, :task_type, NULL, CAST(:scope AS jsonb),
                      :status, :attempt_count, :failure_code,
                      :started_at, :finished_at
                    )
                    """
                ),
                {
                    "id": uuid.UUID(str(task["task_id"])),
                    "job_id": job_id,
                    "task_type": (
                        "EMBEDDED_JAR"
                        if key.startswith("jar:")
                        else (
                            "DERIVED_SOURCE"
                            if key.startswith("derived:")
                            else "COMPONENT"
                        )
                    ),
                    "scope": _json(
                        {
                            "analysis_unit": key,
                            "source_files": task.get("source_files", []),
                            "claims_accepted": task.get("claims_accepted", 0),
                            "claims_rejected": task.get("claims_rejected", 0),
                            "contradictions": task.get("contradictions", []),
                            "missing_knowledge": task.get("missing_knowledge", []),
                            "failure_history": task.get("failure_history", []),
                            "retry_history": task.get("retry_history", []),
                            "additional_evidence_requests": task.get(
                                "additional_evidence_requests", []
                            ),
                            "codex_intervened": task.get("codex_intervened", False),
                            "codex_source_scope": task.get("codex_source_scope", []),
                            "codex_claims_authored": task.get(
                                "codex_claims_authored", 0
                            ),
                            "codex_intervention_policy": task.get(
                                "codex_intervention_policy",
                                "RECORD_FAILURE_AND_CONTINUE",
                            ),
                            "codex_source_substitution": task.get(
                                "codex_source_substitution", False
                            ),
                            "failure_recorded_and_continued": task.get(
                                "failure_recorded_and_continued", False
                            ),
                        }
                    ),
                    "status": task.get(
                        "status", "ADDITIONAL_ANALYSIS_REQUIRED"
                    ),
                    "attempt_count": int(task.get("attempts", 0)),
                    "failure_code": task.get("failure_code"),
                    "started_at": task.get("started_at"),
                    "finished_at": task.get("finished_at"),
                },
            )
        for claim in manifest.claims:
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_analysis_claim (
                      id, job_id, claim_text, claim_type, component, evidence,
                      related_configs, assumptions, unknowns, counter_evidence,
                      confidence, validation_status, validation_errors, created_at
                    ) VALUES (
                      :id, :job_id, :claim_text, :claim_type, :component,
                      CAST(:evidence AS jsonb), CAST(:related_configs AS jsonb),
                      CAST(:assumptions AS jsonb), CAST(:unknowns AS jsonb),
                      CAST(:counter_evidence AS jsonb), :confidence,
                      :validation_status, CAST(:validation_errors AS jsonb), :created_at
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "job_id": job_id,
                    "claim_text": claim.claim,
                    "claim_type": claim.claim_type,
                    "component": claim.component,
                    "evidence": _json([asdict(item) for item in claim.evidence]),
                    "related_configs": _json(claim.related_configs),
                    "assumptions": _json(claim.assumptions),
                    "unknowns": _json(claim.unknowns),
                    "counter_evidence": _json(claim.counter_evidence),
                    "confidence": claim.confidence.value,
                    "validation_status": claim.validation_status.value,
                    "validation_errors": _json(claim.validation_errors),
                    "created_at": now,
                },
            )

    def _persist_evaluation(
        self,
        manifest: AnalysisManifest,
        project_id: uuid.UUID,
        now: datetime,
    ) -> None:
        for case in manifest.evaluation_cases:
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_evaluation_case (
                      id, project_id, question, question_type, expected_evidence,
                      required_files, acceptable_answer, forbidden_assertions,
                      grading_criteria, difficulty, scenario_type, created_at
                    ) VALUES (
                      :id, :project_id, :question, :question_type,
                      CAST(:expected_evidence AS jsonb), CAST(:required_files AS text[]),
                      :acceptable_answer, CAST(:forbidden_assertions AS jsonb),
                      CAST(:grading_criteria AS jsonb), :difficulty, :scenario_type,
                      :created_at
                    )
                    """
                ),
                {
                    "id": case.id,
                    "project_id": project_id,
                    "question": case.question,
                    "question_type": case.question_type,
                    "expected_evidence": _json(
                        [asdict(item) for item in case.expected_evidence]
                    ),
                    "required_files": case.required_files,
                    "acceptable_answer": case.acceptable_answer,
                    "forbidden_assertions": _json(case.forbidden_assertions),
                    "grading_criteria": _json(case.grading_criteria),
                    "difficulty": case.difficulty,
                    "scenario_type": case.scenario_type,
                    "created_at": now,
                },
            )
        for result in manifest.evaluation_results:
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_evaluation_result (
                      id, case_id, snapshot_id, passed, score, failure_category,
                      details, duration_ms, created_at
                    ) VALUES (
                      :id, :case_id, :snapshot_id, :passed, :score,
                      :failure_category, CAST(:details AS jsonb), :duration_ms,
                      :created_at
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "case_id": result.case_id,
                    "snapshot_id": manifest.snapshot_id,
                    "passed": result.passed,
                    "score": result.score,
                    "failure_category": result.failure_category,
                    "details": _json(result.details),
                    "duration_ms": result.duration_ms,
                    "created_at": now,
                },
            )
        for item in manifest.knowledge_items:
            self.session.execute(
                text(
                    """
                    INSERT INTO repository_knowledge_item (
                      id, snapshot_id, document_id, knowledge_type, title, summary,
                      detail, processing_steps, components, configurations,
                      dependencies, source_references, validation_status, confidence,
                      unknowns, analysis_version, prompt_version, searchable, created_at
                    ) VALUES (
                      :id, :snapshot_id, NULL, :knowledge_type, :title, :summary,
                      :detail, CAST(:processing_steps AS jsonb), CAST(:components AS text[]),
                      CAST(:configurations AS text[]), CAST(:dependencies AS text[]),
                      CAST(:source_references AS jsonb), :validation_status, :confidence,
                      CAST(:unknowns AS jsonb), :analysis_version, :prompt_version,
                      :searchable, :created_at
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "snapshot_id": manifest.snapshot_id,
                    "knowledge_type": item.knowledge_type,
                    "title": item.title,
                    "summary": item.summary,
                    "detail": item.detail,
                    "processing_steps": _json(item.processing_steps),
                    "components": item.components,
                    "configurations": item.configurations,
                    "dependencies": item.dependencies,
                    "source_references": _json(
                        [asdict(reference) for reference in item.source_references]
                    ),
                    "validation_status": item.validation_status.value,
                    "confidence": item.confidence.value,
                    "unknowns": _json(item.unknowns),
                    "analysis_version": item.analysis_version,
                    "prompt_version": item.prompt_version,
                    "searchable": item.searchable,
                    "created_at": now,
                },
            )
