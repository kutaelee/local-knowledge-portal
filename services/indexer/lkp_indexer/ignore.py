from pathlib import Path

import pathspec

DEFAULT_IGNORES = [
    ".git/",
    "node_modules/",
    ".next/",
    # Next.js alternate build directories such as .next-prod-v24 are derived.
    "**/.next*/",
    ".turbo/",
    "dist/",
    "build/",
    "out/",
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
    # Playwright/Chromium profiles contain LevelDB and browser caches, not logs
    # intended for human retrieval.
    "**/playwright-profile*/",
    "**/.playwright/",
    # Generated tokenizer payloads are model artifacts rather than human knowledge.
    # They can contain tens of thousands of merge/vocabulary records and otherwise
    # monopolize a single embedding job for many minutes.
    "**/tokenizer_configs/",
    "**/tokenizer/merges.txt",
    "**/tokenizer/vocab.json",
    "**/tokenizer/tokenizer.json",
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
