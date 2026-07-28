from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

_LOCAL_HOSTS = {
    "127.0.0.1",
    "localhost",
    "::1",
    "host.docker.internal",
    "ollama",
}

_REFERENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "file": {"type": "string"},
        "source_hash": {"type": "string"},
        "start_line": {"type": "integer"},
        "end_line": {"type": "integer"},
        "symbol": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["file", "source_hash", "start_line", "end_line", "symbol"],
    "additionalProperties": False,
}

_ANALYSIS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "claim": {"type": "string"},
                    "claim_type": {
                        "type": "string",
                        "enum": [
                            "CODE_FACT",
                            "CONFIGURATION_FACT",
                            "DEPENDENCY_FACT",
                            "DESIGN_INFERENCE",
                            "RUNTIME_FACT",
                            "TROUBLESHOOTING_GUIDE",
                            "UNVERIFIED_HYPOTHESIS",
                        ],
                    },
                    "component": {"type": "string"},
                    "evidence": {
                        "type": "array",
                        "items": _REFERENCE_SCHEMA,
                    },
                    "related_configs": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {"type": "string"},
                                "file": {"type": "string"},
                                "used_by": {"type": "string"},
                            },
                            "required": ["key", "file", "used_by"],
                            "additionalProperties": False,
                        },
                    },
                    "assumptions": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "unknowns": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "counter_evidence": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["HIGH", "MEDIUM", "LOW"],
                    },
                },
                "required": [
                    "claim",
                    "claim_type",
                    "component",
                    "evidence",
                    "related_configs",
                    "assumptions",
                    "unknowns",
                    "counter_evidence",
                    "confidence",
                ],
                "additionalProperties": False,
            },
        },
        "contradictions": {
            "type": "array",
            "items": {"type": "string"},
        },
        "missing_knowledge": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["claims", "contradictions", "missing_knowledge"],
    "additionalProperties": False,
}

_SUPPORT_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **{
            field: {
                "type": "array",
                "items": {"type": "string"},
            }
            for field in (
                "confirmed_facts",
                "hypotheses",
                "counter_evidence",
                "configurations",
                "additional_data",
                "next_steps",
            )
        },
        "source_references": {
            "type": "array",
            "items": _REFERENCE_SCHEMA,
        },
        "confidence": {
            "type": "string",
            "enum": ["HIGH", "MEDIUM", "LOW"],
        },
    },
    "required": [
        "confirmed_facts",
        "hypotheses",
        "counter_evidence",
        "source_references",
        "configurations",
        "additional_data",
        "next_steps",
        "confidence",
    ],
    "additionalProperties": False,
}


def _response_format(context: dict[str, Any]) -> dict[str, Any]:
    support_answer = "question_type" in context
    return {
        "type": "json_schema",
        "json_schema": {
            "name": (
                "repository_support_answer"
                if support_answer
                else "repository_analysis"
            ),
            "strict": True,
            "schema": (
                _SUPPORT_RESPONSE_SCHEMA
                if support_answer
                else _ANALYSIS_RESPONSE_SCHEMA
            ),
        },
    }


@dataclass(frozen=True)
class ModelInvocation:
    payload: dict[str, Any]
    model: str
    prompt_version: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None


class LocalModelProvider:
    """Bounded OpenAI-compatible local provider with no cloud fallback."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 90,
        max_context: int = 32_768,
        max_output: int = 2048,
        max_concurrency: int = 1,
        prompt_version: str = "repo-analysis-v1",
        model_quantization: str | None = None,
        include_source_excerpts: bool = False,
        max_source_chars: int = 80_000,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in _LOCAL_HOSTS:
            raise ValueError("repository analysis model endpoint must be local")
        if not model.strip():
            raise ValueError("repository analysis model name is required")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = min(max(timeout_seconds, 1), 300)
        self.max_context = min(max(max_context, 1_024), 1_000_000)
        self.max_output = min(max(max_output, 128), 16_384)
        self.max_concurrency = min(max(max_concurrency, 1), 16)
        self.prompt_version = prompt_version
        self.model_quantization = model_quantization
        self.include_source_excerpts = include_source_excerpts
        self.max_source_chars = min(max(max_source_chars, 0), 500_000)

    @classmethod
    def from_environment(cls) -> LocalModelProvider | None:
        enabled = os.getenv("REPO_ANALYSIS_MODEL_ENABLED", "false").casefold()
        if enabled not in {"1", "true", "yes", "on"}:
            return None
        return cls(
            base_url=os.getenv(
                "REPO_ANALYSIS_MODEL_BASE_URL",
                "http://host.docker.internal:11434/v1",
            ),
            model=os.environ["REPO_ANALYSIS_MODEL_NAME"],
            timeout_seconds=float(
                os.getenv("REPO_ANALYSIS_MODEL_TIMEOUT_SECONDS", "90")
            ),
            max_context=int(os.getenv("REPO_ANALYSIS_MODEL_MAX_CONTEXT", "32768")),
            max_output=int(os.getenv("REPO_ANALYSIS_MODEL_MAX_OUTPUT", "2048")),
            max_concurrency=int(
                os.getenv("REPO_ANALYSIS_MODEL_MAX_CONCURRENCY", "1")
            ),
            model_quantization=os.getenv("REPO_ANALYSIS_MODEL_QUANTIZATION"),
            prompt_version=os.getenv(
                "REPO_ANALYSIS_MODEL_PROMPT_VERSION",
                "repo-analysis-v2-evidence-excerpts",
            ),
            include_source_excerpts=os.getenv(
                "REPO_ANALYSIS_MODEL_INCLUDE_SOURCE_EXCERPTS",
                "false",
            ).casefold()
            in {"1", "true", "yes", "on"},
            max_source_chars=int(
                os.getenv("REPO_ANALYSIS_MODEL_MAX_SOURCE_CHARS", "80000")
            ),
        )

    def analyze(self, *, system: str, context: dict[str, Any]) -> ModelInvocation:
        started = time.perf_counter()
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": json.dumps(context, ensure_ascii=False),
                    },
                ],
                "temperature": 0,
                "max_tokens": self.max_output,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": _response_format(context),
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("local model response must be a JSON object")
        usage = body.get("usage", {})
        return ModelInvocation(
            payload=payload,
            model=self.model,
            prompt_version=self.prompt_version,
            latency_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )
