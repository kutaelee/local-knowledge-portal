from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    key: str
    relative_path: str
    root: Path


def git_repository_roots(path: Path, source_root: Path) -> list[Path]:
    canonical = path.resolve(strict=False)
    root = source_root.resolve(strict=False)
    canonical.relative_to(root)
    current = canonical.parent
    repository_roots: list[Path] = []
    while current != root.parent:
        if (current / ".git").exists():
            repository_roots.append(current)
        if current == root:
            break
        current = current.parent
    return repository_roots


def project_identity(path: Path, source_root: Path) -> ProjectIdentity:
    canonical = path.resolve(strict=False)
    root = source_root.resolve(strict=False)
    canonical.relative_to(root)
    repository_roots = git_repository_roots(canonical, root)
    if repository_roots:
        repository = repository_roots[-1]
        return ProjectIdentity(
            key=repository.name,
            relative_path=canonical.relative_to(repository).as_posix(),
            root=repository,
        )
    relative = canonical.relative_to(root)
    if len(relative.parts) > 1:
        project_root = root / relative.parts[0]
        return ProjectIdentity(
            key=relative.parts[0],
            relative_path=Path(*relative.parts[1:]).as_posix(),
            root=project_root,
        )
    return ProjectIdentity(
        key=root.name,
        relative_path=relative.as_posix(),
        root=root,
    )
