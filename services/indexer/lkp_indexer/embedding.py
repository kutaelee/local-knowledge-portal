import hashlib
import math
import random
import time
from collections import OrderedDict
from collections.abc import Callable
from threading import Lock
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

    def __init__(
        self,
        base_url: str,
        model: str,
        digest: str,
        dimension: int,
        timeout_seconds: float = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.digest = digest
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = httpx.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": texts},
            timeout=self.timeout_seconds,
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


class CachedEmbedder:
    """Bounded, revision-aware query cache with serialized provider misses."""

    def __init__(
        self,
        delegate: Embedder,
        max_entries: int,
        ttl_seconds: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("embedding cache must allow at least one entry")
        if ttl_seconds <= 0:
            raise ValueError("embedding cache TTL must be positive")
        self.delegate = delegate
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.monotonic = monotonic
        self.provider = delegate.provider
        self.model = delegate.model
        self.digest = delegate.digest
        self.dimension = delegate.dimension
        self._cache: OrderedDict[str, tuple[float, list[float]]] = OrderedDict()
        self._lock = Lock()
        self.hits = 0
        self.misses = 0

    def _key(self, text: str) -> str:
        identity = (
            f"{self.provider}\0{self.model}\0{self.digest}\0"
            f"{self.dimension}\0{text}"
        )
        return hashlib.sha256(identity.encode()).hexdigest()

    def cache_info(self) -> dict[str, int | float]:
        with self._lock:
            return {
                "entries": len(self._cache),
                "hits": self.hits,
                "misses": self.misses,
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl_seconds,
            }

    def embed(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            now = self.monotonic()
            results: list[list[float] | None] = [None] * len(texts)
            missing_texts: list[str] = []
            missing_indexes: list[int] = []
            for index, value in enumerate(texts):
                key = self._key(value)
                cached = self._cache.get(key)
                if cached is not None and cached[0] > now:
                    self._cache.move_to_end(key)
                    results[index] = cached[1]
                    self.hits += 1
                    continue
                if cached is not None:
                    del self._cache[key]
                missing_texts.append(value)
                missing_indexes.append(index)
                self.misses += 1

            if missing_texts:
                vectors = self.delegate.embed(missing_texts)
                for index, value, vector in zip(
                    missing_indexes,
                    missing_texts,
                    vectors,
                    strict=True,
                ):
                    results[index] = vector
                    self._cache[self._key(value)] = (
                        now + self.ttl_seconds,
                        vector,
                    )
                    self._cache.move_to_end(self._key(value))
                    while len(self._cache) > self.max_entries:
                        self._cache.popitem(last=False)
            return [vector for vector in results if vector is not None]


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
