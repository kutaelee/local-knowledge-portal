from __future__ import annotations

import enum
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AnalysisStage(str, enum.Enum):
    DISCOVERING = "DISCOVERING"
    INDEXING = "INDEXING"
    PLANNING = "PLANNING"
    ANALYZING = "ANALYZING"
    VALIDATING = "VALIDATING"
    SYNTHESIZING = "SYNTHESIZING"
    EMBEDDING = "EMBEDDING"
    EVALUATING = "EVALUATING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ValidationStatus(str, enum.Enum):
    SOURCE_VERIFIED = "SOURCE_VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    TEST_VERIFIED = "TEST_VERIFIED"
    RUNTIME_VERIFIED = "RUNTIME_VERIFIED"
    HUMAN_APPROVED = "HUMAN_APPROVED"
    CONTRADICTION = "CONTRADICTION"
    ADDITIONAL_DATA_NEEDED = "ADDITIONAL_DATA_NEEDED"
    ADDITIONAL_ANALYSIS_REQUIRED = "ADDITIONAL_ANALYSIS_REQUIRED"
    RUNTIME_EVIDENCE_REQUIRED = "RUNTIME_EVIDENCE_REQUIRED"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    UNRESOLVED = "UNRESOLVED"
    REJECTED = "REJECTED"
    STALE = "STALE"


class Confidence(str, enum.Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True)
class SourceFile:
    relative_path: str
    content_hash: str
    language: str
    module: str | None
    line_count: int
    size_bytes: int


@dataclass(frozen=True)
class SourceSymbol:
    relative_path: str
    symbol: str
    symbol_type: str
    start_line: int
    end_line: int
    signature_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceRelation:
    source_symbol: str
    target_symbol: str
    relation_type: str
    provenance: str
    relative_path: str
    line: int | None = None


@dataclass(frozen=True)
class ConfigurationReference:
    key: str
    relative_path: str
    declaration_line: int | None
    referenced_by: tuple[str, ...] = ()
    has_default: bool = False
    runtime_value_verified: bool = False


@dataclass(frozen=True)
class DependencyArtifact:
    name: str
    version: str | None
    artifact_type: str
    classification: str
    relative_path: str
    scope: str | None = None
    analysis_status: str = "ANALYZED"


@dataclass(frozen=True)
class RepositoryComponent:
    key: str
    display_name: str
    component_type: str
    responsibility: str
    relative_paths: tuple[str, ...]
    entry_points: tuple[str, ...]
    evidence: tuple[EvidenceReference, ...]
    validation_status: ValidationStatus
    confidence: Confidence


@dataclass(frozen=True)
class LifecycleNode:
    key: str
    phase: str
    title: str
    description: str
    component_key: str
    sequence: int
    evidence: tuple[EvidenceReference, ...]
    validation_status: ValidationStatus
    confidence: Confidence


@dataclass(frozen=True)
class LifecycleEdge:
    source_key: str
    target_key: str
    relation_type: str
    label: str
    provenance: str
    evidence: tuple[EvidenceReference, ...]


@dataclass(frozen=True)
class DependencyUsage:
    dependency_name: str
    component_key: str
    usage_type: str
    relative_path: str
    line: int | None
    provenance: str


@dataclass(frozen=True)
class EvidenceReference:
    file: str
    source_hash: str
    start_line: int
    end_line: int
    symbol: str | None = None


@dataclass
class Claim:
    claim: str
    claim_type: str
    component: str
    evidence: list[EvidenceReference]
    related_configs: list[str | dict[str, str]] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    unknowns: list[str] = field(default_factory=list)
    counter_evidence: list[str] = field(default_factory=list)
    confidence: Confidence = Confidence.MEDIUM
    validation_status: ValidationStatus = ValidationStatus.ADDITIONAL_DATA_NEEDED
    validation_errors: list[str] = field(default_factory=list)


@dataclass
class KnowledgeItem:
    knowledge_type: str
    title: str
    summary: str
    detail: str
    processing_steps: list[str]
    components: list[str]
    configurations: list[str]
    dependencies: list[str]
    source_references: list[EvidenceReference]
    validation_status: ValidationStatus
    confidence: Confidence
    unknowns: list[str]
    analysis_version: str
    prompt_version: str | None = None

    @property
    def searchable(self) -> bool:
        return self.validation_status in {
            ValidationStatus.SOURCE_VERIFIED,
            ValidationStatus.PARTIALLY_VERIFIED,
            ValidationStatus.TEST_VERIFIED,
            ValidationStatus.RUNTIME_VERIFIED,
            ValidationStatus.HUMAN_APPROVED,
            ValidationStatus.ADDITIONAL_DATA_NEEDED,
        }


@dataclass
class EvaluationCase:
    id: uuid.UUID
    question: str
    question_type: str
    expected_evidence: list[EvidenceReference]
    required_files: list[str]
    acceptable_answer: str
    forbidden_assertions: list[str]
    grading_criteria: dict[str, Any]
    difficulty: str
    scenario_type: str | None = None


@dataclass
class EvaluationResult:
    case_id: uuid.UUID
    passed: bool
    score: float
    failure_category: str | None
    details: dict[str, Any]
    duration_ms: int


@dataclass
class AnalysisManifest:
    project_id: uuid.UUID
    snapshot_id: uuid.UUID
    canonical_name: str
    display_name: str
    source_reference: str
    repository_origin_hash: str
    source_hash: str
    snapshot_name: str
    git_commit: str | None
    git_branch: str | None
    dirty_worktree: bool
    started_at: datetime
    completed_at: datetime | None = None
    stage: AnalysisStage = AnalysisStage.DISCOVERING
    files: list[SourceFile] = field(default_factory=list)
    symbols: list[SourceSymbol] = field(default_factory=list)
    relations: list[SourceRelation] = field(default_factory=list)
    configurations: list[ConfigurationReference] = field(default_factory=list)
    dependencies: list[DependencyArtifact] = field(default_factory=list)
    components: list[RepositoryComponent] = field(default_factory=list)
    lifecycle_nodes: list[LifecycleNode] = field(default_factory=list)
    lifecycle_edges: list[LifecycleEdge] = field(default_factory=list)
    dependency_usages: list[DependencyUsage] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    knowledge_items: list[KnowledgeItem] = field(default_factory=list)
    evaluation_cases: list[EvaluationCase] = field(default_factory=list)
    evaluation_results: list[EvaluationResult] = field(default_factory=list)
    skipped_files: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    correlation_id: uuid.UUID = field(default_factory=uuid.uuid4)
    analysis_version: str = "repository-analysis-v2"
    model: str | None = None
    model_quantization: str | None = None
    prompt_version: str | None = None

    def finish(self) -> None:
        self.stage = AnalysisStage.COMPLETED
        self.completed_at = utcnow()
        self.metrics.update(
            {
                "files": len(self.files),
                "symbols": len(self.symbols),
                "relations": len(self.relations),
                "configurations": len(self.configurations),
                "dependencies": len(self.dependencies),
                "components": len(self.components),
                "lifecycle_nodes": len(self.lifecycle_nodes),
                "lifecycle_edges": len(self.lifecycle_edges),
                "dependency_usages": len(self.dependency_usages),
                "claims": len(self.claims),
                "verified_claims": sum(
                    claim.validation_status == ValidationStatus.SOURCE_VERIFIED
                    for claim in self.claims
                ),
                "knowledge_items": len(self.knowledge_items),
                "searchable_knowledge_items": sum(
                    item.searchable for item in self.knowledge_items
                ),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        def normalize(value: Any) -> Any:
            if isinstance(value, enum.Enum):
                return value.value
            if isinstance(value, (datetime, uuid.UUID)):
                return str(value)
            if isinstance(value, tuple):
                return [normalize(item) for item in value]
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            return value

        return normalize(asdict(self))
