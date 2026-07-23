import hashlib
import math
import random
import time
from collections.abc import Callable
from typing import Protocol

import httpx


class DimensionMismatch(RuntimeError):
    pass


class Embedder(Protocol):
    provider: str
    model: str
    digest: str
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OllamaEmbedder:
    provider = "ollama"

    def __init__(self, base_url: str, model: str, digest: str, dimension: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.digest = digest
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": texts},
            timeout=120,
        )
        response.raise_for_status()
        vectors = response.json()["embeddings"]
        if any(len(vector) != self.dimension for vector in vectors):
            actual = sorted({len(vector) for vector in vectors})
            raise DimensionMismatch(
                f"configured dimension {self.dimension}, provider returned {actual}; "
                "reindex required"
            )
        return vectors


class RateLimitedEmbedder:
    """Bound each model request and introduce deterministic cooling between batches."""

    def __init__(
        self,
        delegate: Embedder,
        batch_size: int,
        cooldown_seconds: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if batch_size < 1:
            raise ValueError("embedding batch size must be at least one")
        if cooldown_seconds < 0:
            raise ValueError("embedding cooldown cannot be negative")
        self.delegate = delegate
        self.batch_size = batch_size
        self.cooldown_seconds = cooldown_seconds
        self.sleep = sleep
        self.provider = delegate.provider
        self.model = delegate.model
        self.digest = delegate.digest
        self.dimension = delegate.dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for offset in range(0, len(texts), self.batch_size):
            batch = texts[offset : offset + self.batch_size]
            vectors.extend(self.delegate.embed(batch))
            if offset + self.batch_size < len(texts) and self.cooldown_seconds:
                self.sleep(self.cooldown_seconds)
        return vectors


class DeterministicTestEmbedder:
    provider = "deterministic-test"
    model = "sha256-prng"
    digest = "test-v1"

    def __init__(self, dimension: int = 1024) -> None:
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = []
        for value in texts:
            seed = int.from_bytes(hashlib.sha256(value.encode()).digest()[:8], "big")
            rng = random.Random(seed)
            vector = [rng.uniform(-1, 1) for _ in range(self.dimension)]
            norm = math.sqrt(sum(item * item for item in vector)) or 1
            result.append([item / norm for item in vector])
        return result
