from __future__ import annotations

from pathlib import Path

from .domain import (
    Claim,
    ConfigurationReference,
    DependencyArtifact,
    SourceFile,
    SourceSymbol,
    ValidationStatus,
)


def validate_claim(
    claim: Claim,
    *,
    root: Path,
    files: list[SourceFile],
    symbols: list[SourceSymbol],
    configurations: list[ConfigurationReference],
    dependencies: list[DependencyArtifact],
    evidence_roots: dict[str, Path] | None = None,
) -> Claim:
    file_index = {item.relative_path: item for item in files}
    symbol_index = {(item.relative_path, item.symbol) for item in symbols}
    config_index: dict[str, list[ConfigurationReference]] = {}
    for item in configurations:
        config_index.setdefault(item.key, []).append(item)
    dependency_index = {item.name for item in dependencies}
    errors: list[str] = []

    if not claim.evidence:
        errors.append("missing_evidence")
    for evidence in claim.evidence:
        source = file_index.get(evidence.file)
        if source is None:
            errors.append(f"missing_file:{evidence.file}")
            continue
        relative = Path(evidence.file)
        evidence_root = None
        if evidence_roots and relative.parts:
            evidence_root = evidence_roots.get(relative.parts[0])
        if evidence_root is not None:
            path = (evidence_root / Path(*relative.parts[1:])).resolve()
            allowed_root = evidence_root
        else:
            path = (root / evidence.file).resolve()
            allowed_root = root
        try:
            path.relative_to(allowed_root)
        except ValueError:
            errors.append(f"path_escape:{evidence.file}")
            continue
        if source.content_hash != evidence.source_hash:
            errors.append(f"source_hash_mismatch:{evidence.file}")
        if evidence.start_line < 1 or evidence.end_line < evidence.start_line:
            errors.append(f"invalid_lines:{evidence.file}")
        elif evidence.end_line > source.line_count:
            errors.append(f"line_out_of_range:{evidence.file}")
        if evidence.symbol and (evidence.file, evidence.symbol) not in symbol_index:
            errors.append(f"missing_symbol:{evidence.file}:{evidence.symbol}")

    for value in claim.related_configs:
        key = str(value.get("key")) if isinstance(value, dict) else value
        matches = config_index.get(key, [])
        if not matches:
            errors.append(f"missing_config:{key}")
            continue
        if isinstance(value, dict):
            relative_path = value.get("file")
            used_by = value.get("used_by")
            if relative_path and not any(
                item.relative_path == relative_path for item in matches
            ):
                errors.append(f"missing_config_file:{key}:{relative_path}")
            if used_by and not any(
                used_by in item.referenced_by for item in matches
            ):
                errors.append(f"missing_config_reference:{key}:{used_by}")
    for assumption in claim.assumptions:
        if assumption.startswith("dependency:"):
            dependency = assumption.removeprefix("dependency:")
            if dependency not in dependency_index:
                errors.append(f"missing_dependency:{dependency}")

    claim.validation_errors = errors
    if claim.counter_evidence:
        claim.validation_status = ValidationStatus.CONTRADICTION
    elif not errors and claim.evidence:
        claim.validation_status = ValidationStatus.SOURCE_VERIFIED
    elif claim.evidence and not any(
        item.startswith(("missing_file", "path_escape")) for item in errors
    ):
        claim.validation_status = ValidationStatus.PARTIALLY_VERIFIED
    elif not claim.evidence:
        claim.validation_status = ValidationStatus.ADDITIONAL_DATA_NEEDED
    else:
        claim.validation_status = ValidationStatus.REJECTED
    return claim
