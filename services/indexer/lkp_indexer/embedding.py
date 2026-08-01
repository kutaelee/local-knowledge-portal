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


class ModelUnloadError(RuntimeError):
    pass


class Embedder(Protocol):
    provider: str
    model: str
    digest: str
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...

    def close(self) -> None: ...


class OllamaEmbedder:
    provider = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        digest: str,
        dimension: int,
        timeout_seconds: float = 120,
        keep_alive: str | int = 0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.digest = digest
        self.dimension = dimension
        self.timeout_seconds = timeout_seconds
        self.keep_alive = keep_alive
        self._performance = {
            "requests": 0,
            "inputs": 0,
            "prompt_tokens": 0,
            "total_duration_ns": 0,
            "load_duration_ns": 0,
        }
        self._residency_checked = False
        self._preexisting_resident = False
        self._request_started = False
        self._unload_verified: bool | None = None

    @staticmethod
    def _model_key(value: str) -> str:
        normalized = value.strip().casefold()
        return normalized if ":" in normalized else f"{normalized}:latest"

    def _is_resident(self) -> bool:
        response = httpx.get(
            f"{self.base_url}/api/ps",
            timeout=min(self.timeout_seconds, 10),
        )
        response.raise_for_status()
        requested = self._model_key(self.model)
        return any(
            self._model_key(str(item.get("name") or item.get("model") or "")) == requested
            for item in response.json().get("models") or []
            if isinstance(item, dict)
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not self._residency_checked:
            self._preexisting_resident = self._is_resident()
            self._residency_checked = True
        self._request_started = True
        response = httpx.post(
            f"{self.base_url}/api/embed",
            json={
                "model": self.model,
                "input": texts,
                "keep_alive": self.keep_alive,
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        vectors = payload["embeddings"]
        self._performance["requests"] += 1
        self._performance["inputs"] += len(texts)
        for source, target in (
            ("prompt_eval_count", "prompt_tokens"),
            ("total_duration", "total_duration_ns"),
            ("load_duration", "load_duration_ns"),
        ):
            value = payload.get(source)
            if isinstance(value, int) and value >= 0:
                self._performance[target] += value
        if any(len(vector) != self.dimension for vector in vectors):
            actual = sorted({len(vector) for vector in vectors})
            raise DimensionMismatch(
                f"configured dimension {self.dimension}, provider returned {actual}; "
                "reindex required"
            )
        return vectors

    def performance_metrics(self) -> dict[str, int | float | None]:
        active_duration = max(
            0,
            self._performance["total_duration_ns"] - self._performance["load_duration_ns"],
        )
        return {
            **self._performance,
            "inputs_per_second": (
                round(self._performance["inputs"] * 1_000_000_000 / active_duration, 3)
                if active_duration
                else None
            ),
            "prompt_tokens_per_second": (
                round(
                    self._performance["prompt_tokens"] * 1_000_000_000 / active_duration,
                    2,
                )
                if active_duration and self._performance["prompt_tokens"]
                else None
            ),
            "preexisting_resident": self._preexisting_resident,
            "unload_verified": self._unload_verified,
        }

    def close(self) -> None:
        """Unload only a model loaded by this instance and verify the result."""

        if not self._request_started or self._preexisting_resident:
            return
        try:
            if not self._is_resident():
                self._unload_verified = True
                return
            response = httpx.post(
                f"{self.base_url}/api/generate",
                json={"model": self.model, "keep_alive": 0},
                timeout=min(self.timeout_seconds, 10),
            )
            response.raise_for_status()
            if self._is_resident():
                self._unload_verified = False
                raise ModelUnloadError(f"model remained resident after unload: {self.model}")
            self._unload_verified = True
        except httpx.HTTPError as exc:
            self._unload_verified = False
            raise ModelUnloadError(f"failed to unload model: {self.model}") from exc


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
            vectors.extend(self._embed_batch(batch))
            if offset + self.batch_size < len(texts) and self.cooldown_seconds:
                self.sleep(self.cooldown_seconds)
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Retry only a timed-out multi-input request as smaller requests.

        Ollama can cancel a whole batched request while CPU-constrained. A
        bounded binary split salvages independent chunks without retrying an
        already failed single input forever. Single inputs still propagate the
        timeout to the durable queue, which applies bounded backoff/dead-letter
        handling.
        """

        try:
            return self.delegate.embed(texts)
        except httpx.TimeoutException:
            if len(texts) == 1:
                raise
            middle = len(texts) // 2
            return self._embed_batch(texts[:middle]) + self._embed_batch(texts[middle:])

    def close(self) -> None:
        close = getattr(self.delegate, "close", None)
        if close is not None:
            close()

    def performance_metrics(self) -> dict[str, int | float | None]:
        metrics = getattr(self.delegate, "performance_metrics", None)
        result = metrics() if metrics is not None else {}
        return {
            **result,
            "batch_size": self.batch_size,
            "cooldown_seconds": self.cooldown_seconds,
        }


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
        identity = f"{self.provider}\0{self.model}\0{self.digest}\0{self.dimension}\0{text}"
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

    def close(self) -> None:
        close = getattr(self.delegate, "close", None)
        if close is not None:
            close()


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

    def close(self) -> None:
        return None
