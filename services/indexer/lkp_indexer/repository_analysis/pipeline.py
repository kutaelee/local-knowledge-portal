from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .analyzers import DEFAULT_PLUGINS, AnalyzerPlugin, analyze_files
from .checkpoint import (
    RepositoryAnalysisCheckpointStore,
    analysis_fingerprint,
)
from .design import build_repository_design
from .discovery import decode_source_bytes, discover
from .domain import (
    AnalysisManifest,
    AnalysisStage,
    Claim,
    Confidence,
    ConfigurationReference,
    DependencyArtifact,
    EvidenceReference,
    KnowledgeItem,
    SourceFile,
    SourceRelation,
    SourceSymbol,
    ValidationStatus,
    utcnow,
)
from .evaluation import (
    build_evaluation_cases,
    evaluate_evidence_integrity,
    evaluate_support_answers,
)
from .provider import LocalModelProvider
from .report import synthesize_repository_report
from .validator import validate_claim

_PROJECT_NAMESPACE = uuid.UUID("fce6c5be-41f7-4d29-a35f-1b67c85019fc")
_SNAPSHOT_NAMESPACE = uuid.UUID("4eb0c9dd-17e5-4573-bc30-f631061ef953")
_CODEX_INTERVENTION_POLICY = "RECORD_FAILURE_AND_CONTINUE"


def _display_name(root: Path) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-")
    return cleaned or "repository"


def _claim_for_symbol(source_hash: str, symbol: Any) -> Claim:
    return Claim(
        claim=(f"{symbol.symbol} is declared as {symbol.symbol_type} in {symbol.relative_path}."),
        claim_type="CODE_FACT",
        component=symbol.symbol,
        evidence=[
            EvidenceReference(
                file=symbol.relative_path,
                source_hash=source_hash,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                symbol=symbol.symbol,
            )
        ],
        confidence=Confidence.HIGH,
    )


class RepositoryAnalysisPipeline:
    """Read-only analysis pipeline that persists no source text."""

    def __init__(
        self,
        *,
        allowed_roots: list[str | Path] | None = None,
        plugins: tuple[AnalyzerPlugin, ...] = DEFAULT_PLUGINS,
        provider: LocalModelProvider | None = None,
        max_claims: int = 500,
        evidence_roots: dict[str, str | Path] | None = None,
        checkpoint_store: RepositoryAnalysisCheckpointStore | None = None,
    ) -> None:
        self.allowed_roots = allowed_roots
        self.plugins = plugins
        self.provider = provider
        self.max_claims = max(1, min(max_claims, 10_000))
        self.checkpoint_store = checkpoint_store
        self.evidence_roots = {
            key: Path(value).expanduser().resolve(strict=True)
            for key, value in (evidence_roots or {}).items()
        }
        for key in self.evidence_roots:
            if not key or "/" in key or "\\" in key:
                raise ValueError("evidence root prefix must be one path segment")

    def run(self, source_root: str | Path) -> AnalysisManifest:
        started_clock = time.perf_counter()
        started_at = utcnow()
        discovery = discover(source_root, allowed_roots=self.allowed_roots)
        evidence_discoveries = {
            prefix: discover(path, allowed_roots=[path])
            for prefix, path in self.evidence_roots.items()
        }
        source_hash = discovery.source_hash
        if evidence_discoveries:
            fingerprint = hashlib.sha256(discovery.source_hash.encode("ascii"))
            for prefix, evidence_discovery in sorted(evidence_discoveries.items()):
                fingerprint.update(prefix.encode("utf-8"))
                fingerprint.update(evidence_discovery.source_hash.encode("ascii"))
            source_hash = fingerprint.hexdigest()
        canonical_name = _display_name(discovery.root)
        project_id = uuid.uuid5(_PROJECT_NAMESPACE, discovery.git.origin_hash)
        snapshot_id = uuid.uuid5(
            _SNAPSHOT_NAMESPACE,
            f"{project_id}:{source_hash}",
        )
        files = list(discovery.files)
        skipped_files = list(discovery.skipped)
        for prefix, evidence_discovery in evidence_discoveries.items():
            files.extend(
                SourceFile(
                    relative_path=f"{prefix}/{item.relative_path}",
                    content_hash=item.content_hash,
                    language=item.language,
                    module=item.module,
                    line_count=item.line_count,
                    size_bytes=item.size_bytes,
                )
                for item in evidence_discovery.files
            )
            skipped_files.extend(
                {
                    **item,
                    "path": f"{prefix}/{item['path']}",
                }
                for item in evidence_discovery.skipped
            )
        manifest = AnalysisManifest(
            project_id=project_id,
            snapshot_id=snapshot_id,
            canonical_name=canonical_name,
            display_name=canonical_name,
            source_reference=str(discovery.root),
            repository_origin_hash=discovery.git.origin_hash,
            source_hash=source_hash,
            snapshot_name=canonical_name,
            git_commit=discovery.git.commit,
            git_branch=discovery.git.branch,
            dirty_worktree=discovery.git.dirty,
            started_at=started_at,
            stage=AnalysisStage.INDEXING,
            files=files,
            skipped_files=skipped_files,
            model=getattr(self.provider, "model", None),
            model_quantization=(getattr(self.provider, "model_quantization", None)),
            prompt_version=getattr(self.provider, "prompt_version", None),
        )
        discovery_ms = int((time.perf_counter() - started_clock) * 1000)

        analysis_started = time.perf_counter()
        manifest.stage = AnalysisStage.ANALYZING
        facts = analyze_files(discovery.root, discovery.files, self.plugins)
        for prefix, evidence_discovery in evidence_discoveries.items():
            extra = analyze_files(
                evidence_discovery.root,
                evidence_discovery.files,
                self.plugins,
            )
            facts.symbols.extend(
                SourceSymbol(
                    relative_path=f"{prefix}/{item.relative_path}",
                    symbol=item.symbol,
                    symbol_type=item.symbol_type,
                    start_line=item.start_line,
                    end_line=item.end_line,
                    signature_hash=item.signature_hash,
                    metadata=item.metadata,
                )
                for item in extra.symbols
            )
            facts.relations.extend(
                SourceRelation(
                    source_symbol=item.source_symbol,
                    target_symbol=item.target_symbol,
                    relation_type=item.relation_type,
                    provenance=item.provenance,
                    relative_path=f"{prefix}/{item.relative_path}",
                    line=item.line,
                )
                for item in extra.relations
            )
            facts.configurations.extend(
                ConfigurationReference(
                    key=item.key,
                    relative_path=f"{prefix}/{item.relative_path}",
                    declaration_line=item.declaration_line,
                    referenced_by=item.referenced_by,
                    has_default=item.has_default,
                    runtime_value_verified=item.runtime_value_verified,
                )
                for item in extra.configurations
            )
            facts.dependencies.extend(
                DependencyArtifact(
                    name=item.name,
                    version=item.version,
                    artifact_type=item.artifact_type,
                    classification=item.classification,
                    relative_path=f"{prefix}/{item.relative_path}",
                    scope=item.scope,
                    analysis_status=item.analysis_status,
                )
                for item in extra.dependencies
            )
            facts.warnings.extend(f"{prefix}/{warning}" for warning in extra.warnings)
        manifest.symbols = facts.symbols
        manifest.relations = facts.relations
        manifest.configurations = facts.configurations
        manifest.dependencies = facts.dependencies
        manifest.warnings.extend(facts.warnings)
        build_repository_design(manifest)
        analysis_ms = int((time.perf_counter() - analysis_started) * 1000)

        manifest.stage = AnalysisStage.PLANNING
        file_hashes = {item.relative_path: item.content_hash for item in manifest.files}
        analysis_tasks = self._analysis_tasks(manifest)
        manifest.metrics.update(
            {
                "planned_analysis_tasks": len(analysis_tasks),
                "planned_analysis_files": sum(len(task["files"]) for task in analysis_tasks),
            }
        )
        if self.provider is None:
            for symbol in manifest.symbols[: self.max_claims]:
                manifest.claims.append(_claim_for_symbol(file_hashes[symbol.relative_path], symbol))
        else:
            self._add_model_claims(manifest, discovery.root, analysis_tasks)

        validation_started = time.perf_counter()
        manifest.stage = AnalysisStage.VALIDATING
        for claim in manifest.claims:
            validate_claim(
                claim,
                root=discovery.root,
                files=manifest.files,
                symbols=manifest.symbols,
                configurations=manifest.configurations,
                dependencies=manifest.dependencies,
                evidence_roots=self.evidence_roots,
            )
        validation_ms = int((time.perf_counter() - validation_started) * 1000)

        manifest.stage = AnalysisStage.SYNTHESIZING
        repository_report = synthesize_repository_report(manifest, self.provider)
        structural_items = self._synthesize_legacy(manifest)
        model_items = self._synthesize_model(manifest) if self.provider is not None else []
        manifest.knowledge_items = [repository_report, *structural_items, *model_items]
        manifest.stage = AnalysisStage.EVALUATING
        manifest.evaluation_cases = build_evaluation_cases(manifest)
        evaluation_metrics: dict[str, Any] = {}
        defer_model_evaluation = os.getenv(
            "REPO_ANALYSIS_DEFER_MODEL_EVALUATION",
            "false",
        ).casefold() in {"1", "true", "yes", "on"}
        if self.provider is None or defer_model_evaluation:
            manifest.evaluation_results = evaluate_evidence_integrity(
                manifest,
                manifest.evaluation_cases,
            )
            if self.provider is not None:
                evaluation_metrics["support_answer_evaluation_deferred"] = True
        else:
            (
                manifest.evaluation_results,
                evaluation_metrics,
            ) = evaluate_support_answers(
                manifest,
                manifest.evaluation_cases,
                self.provider,
            )
        manifest.finish()
        derived_replacements: list[dict[str, Any]] = []
        for prefix, evidence_root in sorted(self.evidence_roots.items()):
            evidence_manifest = evidence_root / "evidence-manifest.json"
            if not evidence_manifest.is_file():
                continue
            try:
                payload = json.loads(evidence_manifest.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                continue
            source_label = payload.get("source_label")
            if isinstance(source_label, str) and source_label:
                source_path = (discovery.root / source_label).resolve()
                if (
                    not source_path.is_file()
                    or not source_path.is_relative_to(discovery.root)
                    or hashlib.sha256(source_path.read_bytes()).hexdigest()
                    != payload.get("source_sha256")
                ):
                    manifest.warnings.append(f"{prefix}:derived_evidence:source_hash_mismatch")
                    continue
                expected_files = {
                    str(item.get("file")): str(item.get("content_sha256"))
                    for item in (payload.get("chunks") or payload.get("members") or [])
                }
                actual_files = {
                    item.relative_path: item.content_hash
                    for item in evidence_discoveries[prefix].files
                    if item.relative_path != "evidence-manifest.json"
                }
                if not expected_files or any(
                    actual_files.get(path) != content_hash
                    for path, content_hash in expected_files.items()
                ):
                    manifest.warnings.append(f"{prefix}:derived_evidence:content_hash_mismatch")
                    continue
                derived_replacements.append(
                    {
                        "source_path": source_label,
                        "evidence_prefix": prefix,
                        "source_sha256": payload.get("source_sha256"),
                        "kind": payload.get("kind"),
                    }
                )
        skipped_paths = {item.get("path") for item in manifest.skipped_files}
        derived_replacements = [
            item for item in derived_replacements if item["source_path"] in skipped_paths
        ]
        resolved_paths = {item["source_path"] for item in derived_replacements}
        unresolved_skipped = [
            item for item in manifest.skipped_files if item.get("path") not in resolved_paths
        ]
        answer_quality_count = sum(
            bool(item.details.get("answer_quality_evaluated"))
            for item in manifest.evaluation_results
        )
        manifest.metrics.update(
            {
                "discovery_ms": discovery_ms,
                "static_analysis_ms": analysis_ms,
                "validation_ms": validation_ms,
                "total_ms": int((time.perf_counter() - started_clock) * 1000),
                "skipped_files": len(manifest.skipped_files),
                "skipped_file_details": manifest.skipped_files,
                "resolved_skipped_files": derived_replacements,
                "unresolved_skipped_files": unresolved_skipped,
                "unresolved_skipped_file_count": len(unresolved_skipped),
                "evaluation_cases": len(manifest.evaluation_cases),
                "evaluation_scenarios": sum(
                    item.scenario_type is not None for item in manifest.evaluation_cases
                ),
                "evaluation_evidence_integrity_passed": sum(
                    item.passed for item in manifest.evaluation_results
                ),
                "evaluation_answer_quality_executed": answer_quality_count,
                "evaluation_answer_quality_passed": sum(
                    item.passed and bool(item.details.get("answer_quality_evaluated"))
                    for item in manifest.evaluation_results
                ),
                "evaluation_model_metrics": evaluation_metrics,
            }
        )
        return manifest

    def _model_source_excerpts(
        self,
        manifest: AnalysisManifest,
        root: Path,
        selected_files: set[str],
        *,
        budget: int,
    ) -> list[dict[str, Any]]:
        assert self.provider is not None
        if not self.provider.include_source_excerpts:
            return []

        remaining = min(self.provider.max_source_chars, budget)
        excerpts: list[dict[str, Any]] = []
        symbols_by_file: dict[str, list[Any]] = {}
        for symbol in manifest.symbols:
            if symbol.relative_path in selected_files:
                symbols_by_file.setdefault(symbol.relative_path, []).append(symbol)

        selected_manifest_files = [
            file for file in manifest.files if file.relative_path in selected_files
        ]
        for file_index, file in enumerate(selected_manifest_files):
            if remaining <= 0:
                break
            files_left = len(selected_manifest_files) - file_index
            file_remaining = min(
                remaining,
                max(512, remaining // max(files_left, 1)),
            )
            path = self._source_path(root, file.relative_path)
            try:
                lines = decode_source_bytes(path.read_bytes())[0].splitlines()
            except (OSError, UnicodeError):
                continue

            windows: list[tuple[int, int]] = []
            for symbol in symbols_by_file.get(file.relative_path, [])[:8]:
                start = max(1, symbol.start_line - 2)
                end = min(len(lines), symbol.end_line + 4)
                windows.append((start, end))
            if not windows and lines:
                windows.append((1, min(len(lines), 200)))
            for start, end in sorted(set(windows)):
                text = "\n".join(
                    f"{line_number}: {lines[line_number - 1]}"
                    for line_number in range(start, end + 1)
                )
                if not text:
                    continue
                text = text[:file_remaining]
                excerpts.append(
                    {
                        "file": file.relative_path,
                        "source_hash": file.content_hash,
                        "start_line": start,
                        "end_line": min(end, start + text.count("\n")),
                        "text": text,
                    }
                )
                remaining -= len(text)
                file_remaining -= len(text)
                if remaining <= 0 or file_remaining <= 0:
                    break
        return excerpts

    def _source_path(self, root: Path, relative_path: str) -> Path:
        relative = Path(relative_path)
        if relative.parts and relative.parts[0] in self.evidence_roots:
            evidence_root = self.evidence_roots[relative.parts[0]]
            return evidence_root / Path(*relative.parts[1:])
        return root / relative

    def _analysis_tasks(self, manifest: AnalysisManifest) -> list[dict[str, Any]]:
        groups: dict[str, list[str]] = {}
        for file in manifest.files:
            parts = Path(file.relative_path).parts
            if parts and parts[0] in self.evidence_roots and len(parts) > 1:
                key = (
                    f"jar:{parts[1]}"
                    if parts[0] == ".decompiled"
                    else f"derived:{parts[0]}:{parts[1]}"
                )
            else:
                key = f"component:{file.module or 'root'}"
            groups.setdefault(key, []).append(file.relative_path)
        tasks: list[dict[str, Any]] = []
        batch_size = 5
        for key, paths in sorted(groups.items()):
            ordered_paths = sorted(paths)
            for offset in range(0, len(ordered_paths), batch_size):
                batch_index = offset // batch_size + 1
                batch_key = f"{key}:batch:{batch_index}"
                tasks.append(
                    {
                        "task_id": str(uuid.uuid5(manifest.snapshot_id, batch_key)),
                        "key": batch_key,
                        "purpose": (
                            "내부 개발 JAR의 실제 클래스·메서드·호출·설정·"
                            "예외 관계를 누락 없이 배치 단위로 분석"
                            if key.startswith("jar:")
                            else (
                                "대형·레거시·아카이브 파생 근거의 실제 내용을 "
                                "원본 해시·오프셋 매니페스트와 함께 분석"
                                if key.startswith("derived:")
                                else "컴포넌트의 진입점·처리 흐름·설정·예외·"
                                "의존 관계를 배치 단위로 분석"
                            )
                        ),
                        "files": ordered_paths[offset : offset + batch_size],
                    }
                )
        return tasks

    @staticmethod
    def _related_config_values(values: Any) -> list[str | dict[str, str]]:
        result: list[str | dict[str, str]] = []
        for value in values if isinstance(values, list) else []:
            if isinstance(value, dict) and value.get("key"):
                result.append(
                    {
                        "key": str(value["key"]),
                        "file": str(value.get("file") or ""),
                        "used_by": str(value.get("used_by") or ""),
                    }
                )
            elif isinstance(value, str):
                result.append(value)
        return result

    def _add_model_claims(
        self,
        manifest: AnalysisManifest,
        root: Path,
        tasks: list[dict[str, Any]],
    ) -> None:
        assert self.provider is not None
        totals = {
            "requests": 0,
            "latency_ms": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "accepted": 0,
            "rejected": 0,
            "unknowns": 0,
            "contradictions": 0,
            "missing_knowledge": 0,
            "failed_tasks": 0,
            "unresolved_tasks": 0,
            "skipped_claim_limit": 0,
        }
        outcomes: list[dict[str, Any]] = []
        remaining_claims = self.max_claims
        checkpoint = None
        restored_tasks: dict[
            str,
            tuple[dict[str, Any], list[Claim]],
        ] = {}
        restored_task_count = 0
        saved_task_count = 0
        if self.checkpoint_store is not None:
            fingerprint = analysis_fingerprint(
                manifest,
                max_claims=self.max_claims,
                max_output=getattr(self.provider, "max_output", None),
                max_source_chars=getattr(
                    self.provider,
                    "max_source_chars",
                    None,
                ),
            )
            checkpoint = self.checkpoint_store.begin(
                manifest,
                fingerprint=fingerprint,
                planned_task_count=len(tasks),
            )
            retry_exhausted = os.getenv(
                "REPO_ANALYSIS_RETRY_EXHAUSTED_CHECKPOINT_TASKS",
                "false",
            ).casefold() in {"1", "true", "yes", "on"}
            if retry_exhausted:
                restored_tasks = self.checkpoint_store.restore(
                    checkpoint,
                    tasks,
                    retry_exhausted=True,
                )
            else:
                restored_tasks = self.checkpoint_store.restore(checkpoint, tasks)
            manifest.metrics.update(
                {
                    "analysis_checkpoint_id": str(checkpoint),
                    "analysis_checkpoint_fingerprint": fingerprint,
                }
            )
        seen_claims: set[tuple[str, tuple[tuple[str, int, int, str | None], ...]]] = set()
        for task in tasks:
            restored = restored_tasks.get(str(task["task_id"]))
            if restored is not None:
                task_outcome, task_claims = restored
                accepted_claims = 0
                for claim in task_claims:
                    claim_key = (
                        claim.claim,
                        tuple(
                            (
                                value.file,
                                value.start_line,
                                value.end_line,
                                value.symbol,
                            )
                            for value in claim.evidence
                        ),
                    )
                    if claim_key in seen_claims:
                        continue
                    seen_claims.add(claim_key)
                    manifest.claims.append(claim)
                    accepted_claims += 1
                    totals["unknowns"] += len(claim.unknowns)
                remaining_claims -= accepted_claims
                totals["requests"] += int(task_outcome.get("model_requests", 0))
                totals["latency_ms"] += int(task_outcome.get("model_latency_ms", 0))
                totals["prompt_tokens"] += int(task_outcome.get("model_prompt_tokens", 0))
                totals["completion_tokens"] += int(task_outcome.get("model_completion_tokens", 0))
                totals["accepted"] += accepted_claims
                totals["rejected"] += int(task_outcome.get("claims_rejected", 0))
                contradictions = task_outcome.get("contradictions", [])
                missing = task_outcome.get("missing_knowledge", [])
                totals["contradictions"] += (
                    len(contradictions) if isinstance(contradictions, list) else 1
                )
                totals["missing_knowledge"] += len(missing) if isinstance(missing, list) else 1
                totals["failed_tasks"] += int(bool(task_outcome.get("request_failures_exhausted")))
                if task_outcome["status"] != "SOURCE_EXTRACTED":
                    totals["unresolved_tasks"] += 1
                if task_outcome.get("failure_code") == "CLAIM_LIMIT_REACHED":
                    totals["skipped_claim_limit"] += 1
                outcomes.append(task_outcome)
                restored_task_count += 1
                continue
            selected = set(task["files"])
            task_symbols = [item for item in manifest.symbols if item.relative_path in selected]
            task_relations = [item for item in manifest.relations if item.relative_path in selected]
            task_configs = [
                item for item in manifest.configurations if item.relative_path in selected
            ]
            task_dependencies = [
                item for item in manifest.dependencies if item.relative_path in selected
            ]
            task_outcome: dict[str, Any] = {
                "task_id": task["task_id"],
                "key": task["key"],
                "source_files": sorted(task["files"]),
                "attempts": 0,
                "status": "ADDITIONAL_ANALYSIS_REQUIRED",
                "failure_code": None,
                "failure_history": [],
                "retry_history": [],
                "additional_evidence_requests": [],
                "claims_accepted": 0,
                "claims_rejected": 0,
                "model_requests": 0,
                "model_latency_ms": 0,
                "model_prompt_tokens": 0,
                "model_completion_tokens": 0,
                "request_failures_exhausted": False,
                "codex_intervened": False,
                "codex_source_scope": [],
                "codex_claims_authored": 0,
                "codex_intervention_policy": _CODEX_INTERVENTION_POLICY,
                "codex_source_substitution": False,
                "failure_recorded_and_continued": False,
                "started_at": utcnow(),
                "finished_at": None,
            }
            if remaining_claims <= 0:
                task_outcome["failure_code"] = "CLAIM_LIMIT_REACHED"
                task_outcome["failure_recorded_and_continued"] = True
                task_outcome["finished_at"] = utcnow()
                totals["unresolved_tasks"] += 1
                totals["skipped_claim_limit"] += 1
                manifest.warnings.append(
                    f"model_task_skipped:{task['task_id']}:CLAIM_LIMIT_REACHED"
                )
                outcomes.append(task_outcome)
                if checkpoint is not None and self.checkpoint_store is not None:
                    self.checkpoint_store.save_task(
                        checkpoint,
                        task_outcome,
                        [],
                    )
                    saved_task_count += 1
                continue
            task_claims: list[Claim] = []
            for attempt in range(2):
                task_outcome["attempts"] = attempt + 1
                source_char_budget = 4_500 if attempt == 0 else 2_250
                if attempt:
                    task_outcome["retry_history"].append(
                        {
                            "attempt": attempt + 1,
                            "strategy": "REDUCED_SOURCE_BUDGET",
                            "requested_source_char_budget": source_char_budget,
                            "trigger_failure_code": task_outcome["failure_code"],
                        }
                    )
                context = {
                    "correlation_id": str(manifest.correlation_id),
                    "task_id": task["task_id"],
                    "analysis_unit": task["key"],
                    "artifact": (
                        task["key"].split(":batch:", 1)[0].removeprefix("jar:")
                        if task["key"].startswith("jar:")
                        else None
                    ),
                    "task_purpose": task["purpose"],
                    "project": manifest.canonical_name,
                    "snapshot": manifest.source_hash,
                    "files": [
                        {
                            "path": item.relative_path,
                            "hash": item.content_hash,
                            "language": item.language,
                            "lines": item.line_count,
                        }
                        for item in manifest.files
                        if item.relative_path in selected
                    ][:5],
                    "symbols": [
                        {
                            "file": item.relative_path,
                            "symbol": item.symbol,
                            "type": item.symbol_type,
                            "start_line": item.start_line,
                            "end_line": item.end_line,
                        }
                        for item in task_symbols[:20]
                    ],
                    "relations": [
                        {
                            "source": item.source_symbol,
                            "target": item.target_symbol,
                            "type": item.relation_type,
                            "file": item.relative_path,
                            "line": item.line,
                        }
                        for item in task_relations[:30]
                    ],
                    "configurations": [
                        {
                            "key": item.key,
                            "file": item.relative_path,
                            "line": item.declaration_line,
                            "referenced_by": list(item.referenced_by),
                        }
                        for item in task_configs[:15]
                    ],
                    "dependencies": [
                        {
                            "name": item.name,
                            "version": item.version,
                            "type": item.artifact_type,
                            "classification": item.classification,
                            "file": item.relative_path,
                        }
                        for item in task_dependencies[:15]
                    ],
                    "source_excerpts": self._model_source_excerpts(
                        manifest,
                        root,
                        selected,
                        budget=source_char_budget,
                    ),
                    "retry_instruction": (
                        None
                        if attempt == 0
                        else (
                            "이전 응답이 실패했습니다. 범위를 줄여 검증 가능한 "
                            "핵심 Claim을 최대 2개만 간결하게 반환하세요."
                        )
                    ),
                    "max_claims": 5 if attempt == 0 else 2,
                }
                try:
                    totals["requests"] += 1
                    task_outcome["model_requests"] += 1
                    invocation = self.provider.analyze(
                        system=(
                            "당신은 이 저장소의 주 분석기다. 제공된 task 범위와 근거만 사용해 "
                            "기술지원에 필요한 진입점, 호출 흐름, 조건/분기, 설정 참조, 예외, "
                            "retry/timeout/transaction, DB·메시지·외부 연계, 실제 dependency/JAR "
                            "사용 관계와 장애 시 확인 항목을 분석하라. JSON 객체만 반환하며 "
                            "claims 배열, contradictions 배열, missing_knowledge 배열을 포함하라. "
                            "각 claim은 claim, claim_type, component, evidence, "
                            "related_configs, assumptions, unknowns, counter_evidence, "
                            "confidence를 포함하고 핵심 claims는 최대 5개만 반환한다. "
                            "evidence는 반드시 제공된 file, symbol, "
                            "숫자 start_line/end_line, source_hash만 인용한다. 코드 사실·추론·"
                            "운영 사실을 구분하고 운영 증거가 없으면 unknown으로 남겨라. "
                            "artifact가 있고 실제 사용 관계를 확인했다면 assumptions에 "
                            "dependency:<artifact의 정확한 값>을 넣어라. "
                            "존재하지 않는 파일·symbol·설정을 만들거나 모순을 임의로 해소하지 마라."
                        ),
                        context=context,
                    )
                    totals["latency_ms"] += invocation.latency_ms
                    totals["prompt_tokens"] += invocation.prompt_tokens or 0
                    totals["completion_tokens"] += invocation.completion_tokens or 0
                    task_outcome["model_latency_ms"] += invocation.latency_ms
                    task_outcome["model_prompt_tokens"] += invocation.prompt_tokens or 0
                    task_outcome["model_completion_tokens"] += invocation.completion_tokens or 0
                    claims_payload = invocation.payload.get("claims")
                    if not isinstance(claims_payload, list):
                        raise ValueError("claims must be an array")
                except Exception as exc:
                    task_outcome["failure_code"] = type(exc).__name__
                    task_outcome["failure_history"].append(
                        {
                            "attempt": attempt + 1,
                            "failure_code": type(exc).__name__,
                            "requested_source_char_budget": source_char_budget,
                        }
                    )
                    manifest.warnings.append(
                        f"model_task_failure:{task['task_id']}:"
                        f"attempt_{attempt + 1}:{type(exc).__name__}"
                    )
                    if attempt == 1:
                        totals["failed_tasks"] += 1
                        task_outcome["request_failures_exhausted"] = True
                    continue

                for item in claims_payload[: min(5, remaining_claims)]:
                    try:
                        evidence = [
                            EvidenceReference(
                                file=str(value["file"]),
                                source_hash=str(value["source_hash"]),
                                start_line=int(value["start_line"]),
                                end_line=int(value["end_line"]),
                                symbol=(str(value["symbol"]) if value.get("symbol") else None),
                            )
                            for value in item.get("evidence", [])
                        ]
                        claim = Claim(
                            claim=str(item["claim"]),
                            claim_type=str(item.get("claim_type", "DESIGN_INFERENCE")),
                            component=str(item.get("component", task["key"])),
                            evidence=evidence,
                            related_configs=self._related_config_values(
                                item.get("related_configs", [])
                            ),
                            assumptions=[str(value) for value in item.get("assumptions", [])],
                            unknowns=[str(value) for value in item.get("unknowns", [])],
                            counter_evidence=[
                                str(value) for value in item.get("counter_evidence", [])
                            ],
                            confidence=Confidence(str(item.get("confidence", "LOW"))),
                        )
                        claim_key = (
                            claim.claim,
                            tuple(
                                (
                                    value.file,
                                    value.start_line,
                                    value.end_line,
                                    value.symbol,
                                )
                                for value in claim.evidence
                            ),
                        )
                        if claim_key in seen_claims:
                            continue
                        seen_claims.add(claim_key)
                        manifest.claims.append(claim)
                        task_claims.append(claim)
                        task_outcome["claims_accepted"] += 1
                        totals["accepted"] += 1
                        totals["unknowns"] += len(claim.unknowns)
                        remaining_claims -= 1
                    except (KeyError, TypeError, ValueError):
                        manifest.warnings.append(
                            f"model_claim_rejected:{task['task_id']}:invalid_schema"
                        )
                        task_outcome["claims_rejected"] += 1
                        totals["rejected"] += 1
                contradictions = invocation.payload.get("contradictions", [])
                missing = invocation.payload.get("missing_knowledge", [])
                task_outcome["additional_evidence_requests"] = (
                    [str(value) for value in missing]
                    if isinstance(missing, list)
                    else [str(missing)]
                )
                totals["contradictions"] += (
                    len(contradictions) if isinstance(contradictions, list) else 1
                )
                totals["missing_knowledge"] += len(missing) if isinstance(missing, list) else 1
                needs_follow_up = bool(contradictions or missing)
                task_outcome["status"] = (
                    "SOURCE_EXTRACTED"
                    if task_outcome["claims_accepted"] and not needs_follow_up
                    else "ADDITIONAL_ANALYSIS_REQUIRED"
                )
                if task_outcome["status"] == "SOURCE_EXTRACTED":
                    task_outcome["failure_code"] = None
                elif missing:
                    task_outcome["failure_code"] = "ADDITIONAL_EVIDENCE_REQUIRED"
                elif contradictions:
                    task_outcome["failure_code"] = "CONTRADICTION_REVIEW_REQUIRED"
                elif task_outcome["failure_code"] is None:
                    task_outcome["failure_code"] = "NO_VERIFIABLE_CLAIMS"
                task_outcome["contradictions"] = contradictions
                task_outcome["missing_knowledge"] = missing
                break
            task_outcome["finished_at"] = utcnow()
            if task_outcome["status"] != "SOURCE_EXTRACTED":
                task_outcome["failure_recorded_and_continued"] = True
                totals["unresolved_tasks"] += 1
            outcomes.append(task_outcome)
            if checkpoint is not None and self.checkpoint_store is not None:
                self.checkpoint_store.save_task(
                    checkpoint,
                    task_outcome,
                    task_claims,
                )
                saved_task_count += 1
        manifest.metrics.update(
            {
                "llm_requests": totals["requests"],
                "llm_latency_ms": totals["latency_ms"],
                "llm_prompt_tokens": totals["prompt_tokens"],
                "llm_completion_tokens": totals["completion_tokens"],
                "llm_tasks": len(tasks),
                "llm_tasks_executed": sum(1 for item in outcomes if item["attempts"] > 0),
                "llm_tasks_skipped_claim_limit": totals["skipped_claim_limit"],
                "llm_failed_tasks": totals["failed_tasks"],
                "llm_unresolved_tasks": totals["unresolved_tasks"],
                "llm_task_outcomes": outcomes,
                "llm_claims_accepted": totals["accepted"],
                "llm_claims_rejected": totals["rejected"],
                "llm_unknowns": totals["unknowns"],
                "llm_contradictions": totals["contradictions"],
                "llm_missing_knowledge": totals["missing_knowledge"],
                "operator_intervention_required": False,
                "analysis_failures_recorded": totals["unresolved_tasks"],
                "codex_intervention_policy": _CODEX_INTERVENTION_POLICY,
                "codex_intervention_tasks": 0,
                "codex_source_files_read": 0,
                "codex_source_substitution_tasks": 0,
                "codex_claims_authored": 0,
                "checkpoint_restored_tasks": restored_task_count,
                "checkpoint_saved_tasks": saved_task_count,
            }
        )
        if checkpoint is not None and self.checkpoint_store is not None:
            self.checkpoint_store.mark_tasks_complete(
                checkpoint,
                manifest.metrics,
            )

    def _synthesize_model(self, manifest: AnalysisManifest) -> list[KnowledgeItem]:
        """Build searchable knowledge only from source-verified model claims."""

        grouped: dict[str, list[Claim]] = {}
        for claim in manifest.claims:
            if claim.validation_status == ValidationStatus.SOURCE_VERIFIED:
                grouped.setdefault(claim.component, []).append(claim)

        items: list[KnowledgeItem] = []
        for component, claims in sorted(grouped.items()):
            references: list[EvidenceReference] = []
            seen_references: set[tuple[str, int, int, str | None]] = set()
            for claim in claims:
                for reference in claim.evidence:
                    key = (
                        reference.file,
                        reference.start_line,
                        reference.end_line,
                        reference.symbol,
                    )
                    if key not in seen_references:
                        seen_references.add(key)
                        references.append(reference)
            configurations = sorted(
                {
                    (str(value["key"]) if isinstance(value, dict) else value)
                    for claim in claims
                    for value in claim.related_configs
                }
            )
            dependencies = sorted(
                {
                    assumption.removeprefix("dependency:")
                    for claim in claims
                    for assumption in claim.assumptions
                    if assumption.startswith("dependency:")
                }
            )
            unknowns = sorted({item for claim in claims for item in claim.unknowns})
            claim_types = {claim.claim_type for claim in claims}
            if "TROUBLESHOOTING_GUIDE" in claim_types:
                knowledge_type = "TROUBLESHOOTING"
            elif "CONFIGURATION_FACT" in claim_types:
                knowledge_type = "CONFIGURATION"
            elif "DEPENDENCY_FACT" in claim_types:
                knowledge_type = "DEPENDENCY"
            else:
                knowledge_type = "COMPONENT_FLOW"
            confidence = (
                Confidence.LOW
                if any(claim.confidence == Confidence.LOW for claim in claims)
                else (
                    Confidence.MEDIUM
                    if any(claim.confidence == Confidence.MEDIUM for claim in claims)
                    else Confidence.HIGH
                )
            )
            detail_lines = [f"- [{claim.claim_type}] {claim.claim}" for claim in claims]
            if configurations:
                detail_lines.append(f"- 관련 설정: {', '.join(configurations)}")
            if dependencies:
                detail_lines.append(f"- 관련 의존성: {', '.join(dependencies)}")
            if unknowns:
                detail_lines.append(f"- 추가 확인 필요: {' / '.join(unknowns)}")
            items.append(
                KnowledgeItem(
                    knowledge_type=knowledge_type,
                    title=f"{component} 기술지원 분석",
                    summary=claims[0].claim,
                    detail="\n".join(detail_lines),
                    processing_steps=[claim.claim for claim in claims],
                    components=[component],
                    configurations=configurations,
                    dependencies=dependencies,
                    source_references=references[:100],
                    validation_status=ValidationStatus.SOURCE_VERIFIED,
                    confidence=confidence,
                    unknowns=unknowns,
                    analysis_version=manifest.analysis_version,
                    prompt_version=manifest.prompt_version,
                )
            )
        return items

    def _synthesize_legacy(self, manifest: AnalysisManifest) -> list[KnowledgeItem]:
        verified = [
            claim
            for claim in manifest.claims
            if claim.validation_status == ValidationStatus.SOURCE_VERIFIED
        ]
        references = [evidence for claim in verified for evidence in claim.evidence]
        component_names = [item.display_name for item in manifest.components]
        component_references = [
            reference for component in manifest.components for reference in component.evidence
        ]
        lifecycle_steps = [
            f"{item.title}: {item.description}"
            for item in sorted(manifest.lifecycle_nodes, key=lambda value: value.sequence)
        ]
        source_hashes = {item.relative_path: item.content_hash for item in manifest.files}

        def relation_references(relation_types: set[str]) -> list[EvidenceReference]:
            return [
                EvidenceReference(
                    file=relation.relative_path,
                    source_hash=source_hashes[relation.relative_path],
                    start_line=max(1, relation.line or 1),
                    end_line=max(1, relation.line or 1),
                    symbol=relation.source_symbol,
                )
                for relation in manifest.relations
                if relation.relation_type in relation_types
                and relation.relative_path in source_hashes
            ][:100]

        items = [
            KnowledgeItem(
                knowledge_type="ARCHITECTURE",
                title=f"{manifest.display_name} 구성 구조",
                summary=(
                    f"{len(manifest.components)}개 구성요소와 "
                    f"{len(manifest.files)}개 파일의 역할을 확인했습니다."
                ),
                detail=(
                    "파일 위치와 진입점을 기준으로 구성요소를 묶고 각 역할을 "
                    "원본 근거와 함께 저장했습니다."
                ),
                processing_steps=[
                    "파일 구조 확인",
                    "구성요소 경계 식별",
                    "진입점과 역할 연결",
                ],
                components=component_names,
                configurations=[],
                dependencies=[],
                source_references=component_references[:100] or references[:100],
                validation_status=(
                    ValidationStatus.SOURCE_VERIFIED
                    if component_references or references
                    else ValidationStatus.ADDITIONAL_DATA_NEEDED
                ),
                confidence=(
                    Confidence.HIGH if component_references or references else Confidence.LOW
                ),
                unknowns=["실행 환경에서만 결정되는 연결"],
                analysis_version=manifest.analysis_version,
            )
        ]
        for component in manifest.components[:100]:
            items.append(
                KnowledgeItem(
                    knowledge_type="COMPONENT",
                    title=component.display_name,
                    summary=component.responsibility,
                    detail=(
                        f"{component.component_type} 구성요소이며 "
                        f"{len(component.relative_paths)}개 파일이 포함됩니다."
                    ),
                    processing_steps=["파일 경계 확인", "진입점 확인", "역할 분류"],
                    components=[component.display_name],
                    configurations=[],
                    dependencies=sorted(
                        {
                            usage.dependency_name
                            for usage in manifest.dependency_usages
                            if usage.component_key == component.key
                        }
                    ),
                    source_references=list(component.evidence),
                    validation_status=component.validation_status,
                    confidence=component.confidence,
                    unknowns=[],
                    analysis_version=manifest.analysis_version,
                )
            )
        if manifest.lifecycle_nodes:
            lifecycle_references = [
                reference for node in manifest.lifecycle_nodes for reference in node.evidence
            ]
            items.append(
                KnowledgeItem(
                    knowledge_type="LOGIC_FLOW",
                    title=f"{manifest.display_name} 처리 생명주기",
                    summary=(
                        f"{len(manifest.lifecycle_nodes)}개 단계와 "
                        f"{len(manifest.lifecycle_edges)}개 연결로 수행 흐름을 구성했습니다."
                    ),
                    detail=(
                        "직접 호출로 확인된 연결과 구조상 추론한 다음 단계를 구분해 저장했습니다."
                    ),
                    processing_steps=lifecycle_steps,
                    components=[
                        node.title
                        for node in sorted(
                            manifest.lifecycle_nodes,
                            key=lambda value: value.sequence,
                        )
                    ],
                    configurations=[],
                    dependencies=[],
                    source_references=lifecycle_references[:100],
                    validation_status=ValidationStatus.PARTIALLY_VERIFIED,
                    confidence=Confidence.MEDIUM,
                    unknowns=["외부 실행 환경에서 추가되는 분기"],
                    analysis_version=manifest.analysis_version,
                )
            )
        if manifest.configurations:
            configuration_references = [
                EvidenceReference(
                    file=configuration.relative_path,
                    source_hash=source_hashes[configuration.relative_path],
                    start_line=max(1, configuration.declaration_line or 1),
                    end_line=max(1, configuration.declaration_line or 1),
                    symbol=configuration.key,
                )
                for configuration in manifest.configurations
                if configuration.relative_path in source_hashes
            ][:100]
            items.append(
                KnowledgeItem(
                    knowledge_type="CONFIGURATION",
                    title=f"{manifest.display_name} 설정 항목",
                    summary=f"{len(manifest.configurations)}개 설정 항목을 확인했습니다.",
                    detail=(
                        "설정 변경 영향을 추적할 수 있도록 이름과 선언 위치를 연결했습니다. "
                        "민감할 수 있는 실제 값은 저장하지 않았습니다."
                    ),
                    processing_steps=["설정 구조 확인", "선언 위치 연결", "변경 영향 확인"],
                    components=[],
                    configurations=[item.key for item in manifest.configurations],
                    dependencies=[],
                    source_references=configuration_references,
                    validation_status=ValidationStatus.SOURCE_VERIFIED,
                    confidence=Confidence.HIGH,
                    unknowns=["실행 시점 값", "환경별 재정의"],
                    analysis_version=manifest.analysis_version,
                )
            )
        if manifest.dependencies:
            items.append(
                KnowledgeItem(
                    knowledge_type="DEPENDENCY",
                    title=f"{manifest.display_name} 의존 관계",
                    summary=(
                        f"{len(manifest.dependencies)}개 선언과 "
                        f"{len(manifest.dependency_usages)}개 사용 위치를 확인했습니다."
                    ),
                    detail=(
                        "패키지 선언과 코드의 가져오기 위치를 연결해 구성요소별 "
                        "의존 범위를 저장했습니다."
                    ),
                    processing_steps=["패키지 선언 확인", "사용 위치 연결", "공유 범위 분류"],
                    components=sorted(
                        {usage.component_key for usage in manifest.dependency_usages}
                    ),
                    configurations=[],
                    dependencies=[item.name for item in manifest.dependencies],
                    source_references=[
                        EvidenceReference(
                            file=usage.relative_path,
                            source_hash=next(
                                source.content_hash
                                for source in manifest.files
                                if source.relative_path == usage.relative_path
                            ),
                            start_line=max(1, usage.line or 1),
                            end_line=max(1, usage.line or 1),
                        )
                        for usage in manifest.dependency_usages[:100]
                    ],
                    validation_status=ValidationStatus.SOURCE_VERIFIED,
                    confidence=Confidence.HIGH,
                    unknowns=["실행 시점의 전이 의존성 선택"],
                    analysis_version=manifest.analysis_version,
                )
            )
        diagnostic_groups = (
            (
                {"THROWS", "HANDLES_EXCEPTION"},
                "ERROR_HANDLING",
                "예외 및 실패 처리",
                "코드에 명시된 예외 발생과 처리 위치를 연결했습니다.",
                ["예외 발생 위치 확인", "처리 위치 확인", "실행 로그와 대조"],
            ),
            (
                {"RETRIES", "SENDS_HTTP"},
                "RETRY_TIMEOUT",
                "외부 요청과 재시도",
                (
                    "외부 요청 및 재시도 코드의 위치를 확인했습니다. "
                    "시간 초과 여부는 실행 로그로 확인해야 합니다."
                ),
                ["외부 요청 위치 확인", "재시도 코드 확인", "시간 초과 로그 확인"],
            ),
            (
                {"READS_DB", "WRITES_DB"},
                "DATA_FLOW",
                "데이터 읽기와 저장",
                "데이터베이스 읽기와 쓰기 위치를 정적 근거로 연결했습니다.",
                ["읽기 위치 확인", "쓰기 위치 확인", "트랜잭션 경계 확인"],
            ),
        )
        for relation_types, knowledge_type, title, detail, steps in diagnostic_groups:
            matching = [
                relation
                for relation in manifest.relations
                if relation.relation_type in relation_types
            ]
            if not matching:
                continue
            items.append(
                KnowledgeItem(
                    knowledge_type=knowledge_type,
                    title=f"{manifest.display_name} {title}",
                    summary=f"{len(matching)}개 코드 위치에서 {title} 근거를 확인했습니다.",
                    detail=detail,
                    processing_steps=steps,
                    components=sorted({relation.source_symbol for relation in matching})[:100],
                    configurations=[],
                    dependencies=[],
                    source_references=relation_references(relation_types),
                    validation_status=ValidationStatus.SOURCE_VERIFIED,
                    confidence=Confidence.HIGH,
                    unknowns=["실행 시점의 실제 원인과 결과"],
                    analysis_version=manifest.analysis_version,
                )
            )
        return items
