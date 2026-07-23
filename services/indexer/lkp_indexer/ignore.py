from pathlib import Path

import pathspec

DEFAULT_IGNORES = [
    ".git/",
    "node_modules/",
    ".next/",
    "dist/",
    "build/",
    "coverage/",
    "target/",
    "bin/",
    "obj/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".cache/",
    ".idea/",
    ".vscode/",
    "vendor/",
    "tmp/",
    "temp/",
]


class IgnoreRules:
    def __init__(self, root: Path, extra_patterns: list[str] | None = None) -> None:
        patterns = list(DEFAULT_IGNORES)
        for name in (".knowledgeignore",):
            candidate = root / name
            if candidate.is_file():
                patterns.extend(
                    line
                    for line in candidate.read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                )
        patterns.extend(extra_patterns or [])
        self.spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)

    def matches(self, relative_path: str, is_dir: bool = False) -> bool:
        normalized = relative_path.replace("\\", "/")
        if is_dir:
            normalized += "/"
        return self.spec.match_file(normalized)
