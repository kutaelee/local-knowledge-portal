import hashlib
import math
import random
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
