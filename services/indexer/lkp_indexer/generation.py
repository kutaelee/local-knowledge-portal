from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

import httpx
from lkp.settings import Settings
from pydantic import BaseModel, Field


class KnowledgeEnrichment(BaseModel):
    summary: str = Field(min_length=1)
    observed_facts: list[str] = Field(default_factory=list)
    extracted_information: list[str] = Field(default_factory=list)
    inferences_needing_confirmation: list[str] = Field(default_factory=list)


@dataclass(slots=True)
class GenerationResult:
    content: KnowledgeEnrichment
    provider: str
    model: str
    model_digest: str


class GenerationProvider(Protocol):
    provider: str
    model: str

    def generate(self, source_text: str) -> GenerationResult: ...


class OllamaGenerationProvider:
    provider = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        configured_digest: str,
        timeout_seconds: int,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not model:
            raise ValueError("LKP_GENERATION_MODEL is required when generation is enabled")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.configured_digest = configured_digest
        self.client = httpx.Client(timeout=timeout_seconds, transport=transport)

    def _model_digest(self) -> str:
        response = self.client.get(f"{self.base_url}/api/tags")
        response.raise_for_status()
        models = response.json().get("models") or []
        match = next(
            (
                item
                for item in models
                if item.get("name") == self.model or item.get("model") == self.model
            ),
            None,
        )
        if not match or not match.get("digest"):
            raise RuntimeError(f"configured Ollama model is not installed: {self.model}")
        actual = str(match["digest"])
        if self.configured_digest not in {"", "unresolved"} and actual != self.configured_digest:
            raise RuntimeError(
                "local generation model digest changed; update the configured revision explicitly"
            )
        return actual

    def generate(self, source_text: str) -> GenerationResult:
        digest = self._model_digest()
        schema = KnowledgeEnrichment.model_json_schema()
        system = (
            "You create evidence-bound knowledge-base metadata. Do not state intent, causality, "
            "or outcomes as fact unless directly present in the source. Put uncertain conclusions "
            "only in inferences_needing_confirmation. Return exactly the supplied JSON schema."
        )
        response = self.client.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": (
                            "JSON schema:\n"
                            f"{json.dumps(schema, ensure_ascii=False)}\n\n"
                            "Source transcript:\n"
                            f"{source_text}"
                        ),
                    },
                ],
                "stream": False,
                "think": False,
                "format": schema,
                "options": {"temperature": 0},
            },
        )
        response.raise_for_status()
        content = response.json().get("message", {}).get("content")
        if not isinstance(content, str):
            raise RuntimeError("Ollama response did not contain message.content")
        return GenerationResult(
            content=KnowledgeEnrichment.model_validate_json(content),
            provider=self.provider,
            model=self.model,
            model_digest=digest,
        )


def build_generation_provider(settings: Settings) -> GenerationProvider | None:
    if settings.generation_provider == "disabled":
        return None
    if settings.generation_provider == "ollama":
        return OllamaGenerationProvider(
            settings.generation_base_url,
            settings.generation_model,
            settings.generation_model_digest,
            settings.generation_timeout_seconds,
        )
    raise ValueError(f"unsupported generation provider: {settings.generation_provider}")
