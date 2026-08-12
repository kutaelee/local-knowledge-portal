#!/usr/bin/env python3
"""Measure repository prompt token counts without calling or loading the model."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from lkp_indexer.repository_analysis.pipeline import RepositoryAnalysisPipeline
from lkp_indexer.repository_analysis.provider import ModelInvocation

_TOKENIZER_PROGRAM = r"""
import json
import sys
from transformers import AutoTokenizer

tokenizer = AutoTokenizer.from_pretrained(sys.argv[1], local_files_only=True)
messages = json.load(sys.stdin)
counts = []
metadata_fields = ("symbols", "relations", "configurations", "dependencies")

def count_tokens(system, context):
    user = json.dumps(context, ensure_ascii=False)
    tokens = tokenizer.apply_chat_template(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=False,
    )
    if hasattr(tokens, "get"):
        tokens = tokens["input_ids"]
    if tokens and isinstance(tokens[0], list):
        tokens = tokens[0]
    return len(tokens)

for item in messages:
    context = item["context"]
    without_source = {**context, "source_excerpts": []}
    without_metadata = {
        **context,
        **{field: [] for field in metadata_fields},
    }
    files_only = {
        **without_source,
        **{field: [] for field in metadata_fields},
    }
    counts.append(
        {
            "full": count_tokens(item["system"], context),
            "without_source": count_tokens(item["system"], without_source),
            "without_metadata": count_tokens(item["system"], without_metadata),
            "files_only": count_tokens(item["system"], files_only),
        }
    )
print(json.dumps(counts))
"""


class _SizingProvider:
    include_source_excerpts = True
    model = "qwen3.6-27b-mtp-q4-k-m"
    model_quantization = "Q4_K_M"
    prompt_version = "repo-analysis-v2-evidence-excerpts"

    def __init__(self, max_source_chars: int) -> None:
        self.max_source_chars = max_source_chars
        self.messages: list[dict[str, Any]] = []

    def analyze(self, *, system: str, context: dict[str, Any]) -> ModelInvocation:
        if "task_id" in context:
            self.messages.append(
                {
                    "system": system,
                    "context": context,
                }
            )
        return ModelInvocation(
            payload=(
                {
                    "confirmed_facts": [],
                    "hypotheses": [],
                    "counter_evidence": [],
                    "source_references": [],
                    "configurations": [],
                    "additional_data": ["사전 크기 측정"],
                    "next_steps": [],
                    "confidence": "LOW",
                }
                if "question_type" in context
                else {
                    "claims": [],
                    "contradictions": [],
                    "missing_knowledge": [],
                }
            ),
            model=self.model,
            prompt_version=self.prompt_version,
            latency_ms=0,
            prompt_tokens=0,
            completion_tokens=0,
        )


def _percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--evidence-root", action="append", default=[])
    parser.add_argument("--runtime-python", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--max-model-len", type=int, default=16_384)
    parser.add_argument("--max-output", type=int, default=4_096)
    parser.add_argument("--max-source-chars", type=int, default=4_500)
    args = parser.parse_args()
    evidence_roots: dict[str, Path] = {}
    for value in args.evidence_root:
        prefix, separator, path = value.partition("=")
        if not separator:
            parser.error("--evidence-root must use PREFIX=PATH")
        evidence_roots[prefix] = Path(path)
    source_root = args.source_root.resolve(strict=True)
    runtime_python = args.runtime_python.expanduser()
    if not runtime_python.is_file():
        raise SystemExit("runtime Python does not exist")
    provider = _SizingProvider(args.max_source_chars)
    manifest = RepositoryAnalysisPipeline(
        allowed_roots=[source_root],
        provider=provider,
        max_claims=10_000,
        evidence_roots=evidence_roots,
    ).run(source_root)
    completed = subprocess.run(
        [
            str(runtime_python),
            "-c",
            _TOKENIZER_PROGRAM,
            str(args.tokenizer.resolve(strict=True)),
        ],
        input=json.dumps(provider.messages, ensure_ascii=False),
        text=True,
        capture_output=True,
        check=True,
        timeout=600,
    )
    measurements = json.loads(completed.stdout)
    counts = [int(value["full"]) for value in measurements]
    without_source = [int(value["without_source"]) for value in measurements]
    without_metadata = [int(value["without_metadata"]) for value in measurements]
    files_only = [int(value["files_only"]) for value in measurements]
    maximum = max(counts, default=0)
    result = {
        "project": manifest.canonical_name,
        "source_hash": manifest.source_hash,
        "planned_tasks": manifest.metrics["planned_analysis_tasks"],
        "measured_prompts": len(counts),
        "input_tokens_min": min(counts, default=0),
        "input_tokens_p50": _percentile(counts, 0.50),
        "input_tokens_p95": _percentile(counts, 0.95),
        "input_tokens_max": maximum,
        "input_tokens_max_without_source": max(without_source, default=0),
        "input_tokens_max_without_metadata": max(without_metadata, default=0),
        "input_tokens_max_files_only": max(files_only, default=0),
        "max_output_tokens": args.max_output,
        "max_model_len": args.max_model_len,
        "within_context_limit": maximum + args.max_output <= args.max_model_len,
        "source_text_stored": False,
    }
    print(json.dumps(result, ensure_ascii=False))
    if not result["within_context_limit"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
