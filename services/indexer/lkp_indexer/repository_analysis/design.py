from __future__ import annotations

import re
from collections import defaultdict
from pathlib import PurePosixPath

from .domain import (
    AnalysisManifest,
    Confidence,
    DependencyUsage,
    EvidenceReference,
    LifecycleEdge,
    LifecycleNode,
    RepositoryComponent,
    ValidationStatus,
)

_PHASE_ORDER = {
    "STARTUP": 10,
    "DISCOVERY": 20,
    "INPUT": 30,
    "PROCESSING": 40,
    "VALIDATION": 50,
    "PERSISTENCE": 60,
    "OUTPUT": 70,
    "SHUTDOWN": 80,
    "SUPPORT": 90,
}

_ROLE_RULES = (
    (
        "STARTUP",
        ("__main__", "main", "bootstrap", "startup", "entry", "cli"),
        "실행 시작",
        "실행 진입점과 초기화 코드가 런타임 구성을 시작합니다.",
    ),
    (
        "DISCOVERY",
        ("discover", "scanner", "scan", "fingerprint", "watcher", "source"),
        "대상 탐색",
        "처리 대상, 리소스 또는 구성 정보를 탐색합니다.",
    ),
    (
        "VALIDATION",
        ("validat", "verify", "guard", "check", "evaluation", "test"),
        "조건 검증",
        "입력, 구성 또는 처리 결과가 요구 조건을 만족하는지 확인합니다.",
    ),
    (
        "PERSISTENCE",
        ("repository", "store", "persist", "database", "db", "migration"),
        "데이터 저장",
        "데이터 저장소와 영속화 경계를 담당합니다.",
    ),
    (
        "OUTPUT",
        ("route", "controller", "api", "view", "page", "present", "search"),
        "외부 제공",
        "라우트, API, 화면 또는 출력 채널을 통해 결과를 외부에 제공합니다.",
    ),
    (
        "SHUTDOWN",
        ("shutdown", "stop", "cleanup", "close"),
        "종료 처리",
        "실행 중 사용한 자원을 정리하고 작업을 종료합니다.",
    ),
    (
        "INPUT",
        ("ingest", "input", "consumer", "receive", "reader", "load"),
        "입력 수집",
        "외부 입력, 메시지, 파일 또는 설정을 읽습니다.",
    ),
    (
        "PROCESSING",
        (
            "pipeline",
            "analy",
            "service",
            "worker",
            "handler",
            "processor",
            "provider",
            "domain",
        ),
        "핵심 처리",
        "저장소의 도메인 규칙과 핵심 처리 로직을 수행합니다.",
    ),
)

_DISPLAY_NAMES = {
    "__main__": "실행 시작",
    "__init__": "모듈 구성",
    "analyzers": "정적 분석",
    "discovery": "파일 탐색",
    "domain": "분석 정보 모델",
    "evaluation": "결과 평가",
    "pipeline": "분석 처리",
    "provider": "로컬 모델 연동",
    "repository": "분석 결과 저장",
    "validator": "근거 검증",
    "api": "조회 API",
    "web": "관리 화면",
    "ui": "공통 화면",
    "indexer": "인덱싱 작업",
    "db": "데이터베이스",
    "infra": "실행 인프라",
    "package": "패키지 정보",
    "pnpm-lock": "패키지 잠금",
    "pnpm-workspace": "패키지 작업공간",
    "pyproject": "파이썬 프로젝트",
    "scripts": "운영 도구",
    "tests": "자동 검증",
}

_TOKEN_NAMES = {
    "activity": "활동",
    "analysis": "분석",
    "article": "문서",
    "backup": "백업",
    "capture": "수집",
    "case": "사례",
    "chat": "대화",
    "chunking": "검색 조각 생성",
    "cli": "명령 실행",
    "cleanup": "정리",
    "codex": "Codex",
    "config": "설정",
    "contracts": "공통 데이터 계약",
    "derived": "파생 데이터",
    "document": "문서",
    "design": "구조 설계",
    "embedding": "임베딩",
    "file": "파일",
    "generation": "생성",
    "health": "상태",
    "hook": "훅",
    "host": "호스트",
    "ignore": "제외 규칙",
    "job": "작업",
    "journal": "작업 기록",
    "knowledge": "지식",
    "link": "연결",
    "local": "로컬",
    "logging": "로그",
    "main": "실행 시작",
    "metadata": "메타데이터",
    "migration": "데이터 이전",
    "models": "데이터 모델",
    "policy": "정책",
    "paths": "경로 관리",
    "project": "프로젝트",
    "projects": "프로젝트 관리",
    "quality": "품질",
    "queue": "작업 큐",
    "record": "기록",
    "recovery": "복구",
    "redaction": "민감정보 제거",
    "reconcile": "전체 대조",
    "resync": "재동기화",
    "repository": "저장소",
    "retention": "보존",
    "routes": "조회 API",
    "runtime": "실행 환경",
    "safety": "안전 검사",
    "scanner": "파일 탐색",
    "schemas": "데이터 형식",
    "search": "검색",
    "selection": "대상 선택",
    "semantic": "의미 검색",
    "service": "서비스",
    "settings": "설정",
    "source": "원본",
    "spool": "수집 대기함",
    "watcher": "변경 감시",
    "worker": "백그라운드 작업",
}


def _component_key(relative_path: str) -> str:
    path = PurePosixPath(relative_path)
    parts = path.parts
    if len(parts) >= 2 and parts[0] in {".decompiled", ".normalized"}:
        return "/".join(parts[:2])
    if len(parts) >= 4 and parts[:3] == ("data", "indigo", "components"):
        return "/".join(parts[:4])
    if len(parts) >= 4 and parts[:3] == ("data", "indigo", "service-assemblies"):
        return "/".join(parts[:4])
    if len(parts) >= 4 and parts[:3] == ("data", "indigo", "sharedlibs"):
        return "/".join(parts[:4])
    if (
        len(parts) >= 4
        and parts[0] == "services"
        and path.suffix in {".py", ".java", ".kt", ".js", ".ts"}
    ):
        return f"{parts[0]}/{parts[1]}/{path.stem}"
    if len(parts) >= 2 and parts[0] in {"apps", "services", "packages"}:
        return "/".join(parts[:2])
    if len(parts) >= 2 and parts[0] in {"db", "scripts", "tests", "config", "docs"}:
        return parts[0]
    if len(parts) > 1:
        return parts[0]
    return path.stem


def _humanize(value: str) -> str:
    leaf = value.split("/")[-1]
    if value.startswith((".decompiled/", ".normalized/")):
        artifact = leaf.split("__", 1)[0]
        return artifact.replace("_", " ").replace("-", " ")
    if leaf in _DISPLAY_NAMES:
        return _DISPLAY_NAMES[leaf]
    tokens = [token for token in re.split(r"[_-]+", leaf.casefold()) if token]
    translated = [_TOKEN_NAMES[token] for token in tokens if token in _TOKEN_NAMES]
    return " ".join(translated) if translated else "기타 구성요소"


def _component_type(key: str, paths: list[str]) -> str:
    joined = " ".join([key, *paths]).casefold()
    if key.startswith("apps/") or any(path.endswith((".tsx", ".jsx")) for path in paths):
        return "화면"
    if key == "db" or "migration" in joined:
        return "데이터베이스"
    if "api" in joined or "route" in joined:
        return "API"
    if "worker" in joined or "indexer" in joined:
        return "백그라운드 작업"
    if key == "scripts":
        return "운영 도구"
    if key == "tests":
        return "검증"
    return "핵심 모듈"


def _role(key: str, paths: list[str]) -> tuple[str, str, str]:
    haystack = " ".join([key, *paths]).casefold()
    for phase, needles, title, description in _ROLE_RULES:
        if any(needle in haystack for needle in needles):
            return phase, title, description
    return (
        "SUPPORT",
        "지원 기능",
        "핵심 처리에 필요한 공통 기능과 데이터 구조를 제공합니다.",
    )


def _evidence(
    manifest: AnalysisManifest,
    relative_path: str,
    *,
    line: int | None = None,
    symbol: str | None = None,
) -> EvidenceReference:
    source = next(item for item in manifest.files if item.relative_path == relative_path)
    start = max(1, line or 1)
    return EvidenceReference(
        file=relative_path,
        source_hash=source.content_hash,
        start_line=start,
        end_line=start if line else max(1, source.line_count),
        symbol=symbol,
    )


def _dependency_key(value: str) -> str:
    value = value.casefold().strip()
    value = value.split(":", 1)[-1]
    value = value.split(".", 1)[0] if value.count(".") > 1 else value
    return re.sub(r"[^a-z0-9]", "", value)


def build_repository_design(manifest: AnalysisManifest) -> None:
    paths_by_component: dict[str, list[str]] = defaultdict(list)
    entries_by_component: dict[str, list[str]] = defaultdict(list)
    for source in manifest.files:
        paths_by_component[_component_key(source.relative_path)].append(source.relative_path)
    for symbol in manifest.symbols:
        key = _component_key(symbol.relative_path)
        leaf = symbol.symbol.rsplit(".", 1)[-1].casefold()
        if leaf in {"main", "run", "start", "startup", "bootstrap"}:
            entries_by_component[key].append(symbol.symbol)

    components: list[RepositoryComponent] = []
    component_roles: dict[str, tuple[str, str, str]] = {}
    for key, paths in sorted(paths_by_component.items()):
        entries = sorted(set(entries_by_component.get(key, [])))
        component_type = _component_type(key, paths)
        if component_type == "화면":
            phase, role_title, responsibility = (
                "OUTPUT",
                "화면 제공",
                "사용자가 결과와 상태를 조회하거나 조작할 수 있는 화면을 제공합니다.",
            )
        elif component_type == "API":
            phase, role_title, responsibility = (
                "OUTPUT",
                "API 제공",
                "외부 호출자가 기능과 상태에 접근할 수 있는 API를 제공합니다.",
            )
        elif component_type == "데이터베이스":
            phase, role_title, responsibility = (
                "PERSISTENCE",
                "데이터 저장",
                "서비스 데이터와 상태를 보존할 데이터 구조를 관리합니다.",
            )
        elif component_type == "운영 도구":
            phase, role_title, responsibility = (
                "SUPPORT",
                "운영 지원",
                "설치, 점검, 백업과 복구 등 운영 절차를 지원합니다.",
            )
        elif component_type == "검증":
            phase, role_title, responsibility = (
                "VALIDATION",
                "근거 검증",
                "분석과 조회 기능이 의도대로 동작하는지 자동 검증합니다.",
            )
        else:
            phase, role_title, responsibility = _role(key, paths)
        component_roles[key] = (phase, role_title, responsibility)
        references = tuple(_evidence(manifest, path) for path in paths[:5])
        components.append(
            RepositoryComponent(
                key=key,
                display_name=_humanize(key),
                component_type=component_type,
                responsibility=responsibility,
                relative_paths=tuple(paths),
                entry_points=tuple(entries),
                evidence=references,
                validation_status=ValidationStatus.SOURCE_VERIFIED,
                confidence=Confidence.HIGH,
            )
        )
    manifest.components = components

    lifecycle_components: dict[str, list[RepositoryComponent]] = defaultdict(list)
    for component in components:
        phase = component_roles[component.key][0]
        if phase != "SUPPORT" and component.key not in {"tests", "docs", "config"}:
            lifecycle_components[phase].append(component)
    if not lifecycle_components:
        for component in components:
            if component.key not in {"tests", "docs"}:
                lifecycle_components["PROCESSING"].append(component)

    lifecycle_nodes: list[LifecycleNode] = []
    for index, phase in enumerate(
        sorted(lifecycle_components, key=lambda value: _PHASE_ORDER[value]),
        1,
    ):
        phase_components = lifecycle_components[phase]
        _, role_title, responsibility = component_roles[phase_components[0].key]
        evidence = tuple(
            reference for component in phase_components for reference in component.evidence[:2]
        )
        lifecycle_nodes.append(
            LifecycleNode(
                key=phase.casefold(),
                phase=phase,
                title=role_title,
                description=(
                    f"{responsibility} 관련 구성요소 "
                    f"{len(phase_components)}개가 이 단계를 수행합니다."
                ),
                component_key=phase_components[0].key,
                sequence=index,
                evidence=evidence[:10],
                validation_status=ValidationStatus.PARTIALLY_VERIFIED,
                confidence=Confidence.MEDIUM,
            )
        )
    manifest.lifecycle_nodes = lifecycle_nodes

    symbol_targets: dict[str, set[str]] = defaultdict(set)
    for symbol in manifest.symbols:
        component = _component_key(symbol.relative_path)
        symbol_targets[symbol.symbol].add(component)
        symbol_targets[symbol.symbol.rsplit(".", 1)[-1]].add(component)

    node_keys = {node.key for node in lifecycle_nodes}
    edge_evidence: dict[tuple[str, str, str], list[EvidenceReference]] = defaultdict(list)
    for relation in manifest.relations:
        if relation.relation_type not in {
            "CALLS",
            "CREATES",
            "STARTED_BY",
            "PUBLISHES",
            "CONSUMES",
            "READS_DB",
            "WRITES_DB",
        }:
            continue
        source_component = _component_key(relation.relative_path)
        source_key = component_roles.get(source_component, ("SUPPORT", "", ""))[0].casefold()
        target_leaf = relation.target_symbol.rsplit(".", 1)[-1]
        target_components = symbol_targets.get(
            relation.target_symbol,
            set(),
        ) | symbol_targets.get(target_leaf, set())
        target_candidates = {
            component_roles.get(item, ("SUPPORT", "", ""))[0].casefold()
            for item in target_components
        }
        target_candidates = {
            item for item in target_candidates if item != source_key and item in node_keys
        }
        if source_key not in node_keys or len(target_candidates) != 1:
            continue
        target_key = next(iter(target_candidates))
        edge_evidence[(source_key, target_key, relation.relation_type)].append(
            _evidence(
                manifest,
                relation.relative_path,
                line=relation.line,
                symbol=relation.source_symbol,
            )
        )

    lifecycle_edges: list[LifecycleEdge] = []
    for (source_key, target_key, relation_type), references in sorted(edge_evidence.items()):
        lifecycle_edges.append(
            LifecycleEdge(
                source_key=source_key,
                target_key=target_key,
                relation_type=relation_type,
                label="직접 호출" if relation_type == "CALLS" else "확인된 연결",
                provenance="STATIC_CONFIRMED",
                evidence=tuple(references[:5]),
            )
        )
    existing_pairs = {(edge.source_key, edge.target_key) for edge in lifecycle_edges}
    for source, target in zip(lifecycle_nodes, lifecycle_nodes[1:], strict=False):
        if (source.key, target.key) in existing_pairs:
            continue
        lifecycle_edges.append(
            LifecycleEdge(
                source_key=source.key,
                target_key=target.key,
                relation_type="NEXT_PHASE",
                label="구조상 다음 단계",
                provenance="STATIC_INFERRED",
                evidence=tuple([*source.evidence[:1], *target.evidence[:1]]),
            )
        )
    manifest.lifecycle_edges = lifecycle_edges

    dependency_names = {
        _dependency_key(item.name): item.name
        for item in manifest.dependencies
        if _dependency_key(item.name)
    }
    usages: dict[tuple[str, str, str, str, int | None], DependencyUsage] = {}
    for dependency in manifest.dependencies:
        component = _component_key(dependency.relative_path)
        usage_type = dependency.scope or "runtime"
        usage = DependencyUsage(
            dependency_name=dependency.name,
            component_key=component,
            usage_type=usage_type,
            relative_path=dependency.relative_path,
            line=None,
            provenance="DECLARED",
        )
        usages[
            (
                usage.dependency_name,
                usage.component_key,
                usage.usage_type,
                usage.relative_path,
                usage.line,
            )
        ] = usage
    for relation in manifest.relations:
        if relation.relation_type not in {"DEPENDS_ON", "IMPORTS"}:
            continue
        target = relation.target_symbol.split(".", 1)[0]
        dependency_name = dependency_names.get(_dependency_key(target))
        if not dependency_name:
            continue
        component = _component_key(relation.relative_path)
        usage = DependencyUsage(
            dependency_name=dependency_name,
            component_key=component,
            usage_type="import",
            relative_path=relation.relative_path,
            line=relation.line,
            provenance="STATIC_CONFIRMED",
        )
        usages[
            (
                usage.dependency_name,
                usage.component_key,
                usage.usage_type,
                usage.relative_path,
                usage.line,
            )
        ] = usage
    manifest.dependency_usages = sorted(
        usages.values(),
        key=lambda item: (
            item.component_key,
            item.dependency_name,
            item.relative_path,
            item.line or 0,
        ),
    )

    manifest.metrics["confirmed_lifecycle_edges"] = sum(
        edge.provenance == "STATIC_CONFIRMED" for edge in lifecycle_edges
    )
    manifest.metrics["inferred_lifecycle_edges"] = sum(
        edge.provenance == "STATIC_INFERRED" for edge in lifecycle_edges
    )
    manifest.metrics["dependency_components"] = len(
        {usage.component_key for usage in manifest.dependency_usages}
    )
