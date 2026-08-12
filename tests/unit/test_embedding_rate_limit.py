from lkp_indexer.embedding import CachedEmbedder, RateLimitedEmbedder


class RecordingEmbedder:
    provider = "recording"
    model = "test"
    digest = "test-v1"
    dimension = 1

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(value))] for value in texts]


def test_rate_limited_embedder_bounds_batches_and_preserves_order():
    delegate = RecordingEmbedder()
    sleeps: list[float] = []
    embedder = RateLimitedEmbedder(delegate, 2, 0.25, sleep=sleeps.append)

    result = embedder.embed(["a", "bb", "ccc", "dddd", "eeeee"])

    assert delegate.calls == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert sleeps == [0.25, 0.25]
    assert result == [[1.0], [2.0], [3.0], [4.0], [5.0]]
    assert embedder.provider == "recording"


def test_rate_limited_embedder_rejects_unsafe_configuration():
    delegate = RecordingEmbedder()

    try:
        RateLimitedEmbedder(delegate, 0, 0)
    except ValueError as exc:
        assert "batch size" in str(exc)
    else:
        raise AssertionError("zero batch size must be rejected")


def test_cached_embedder_reuses_revision_scoped_value_until_ttl():
    delegate = RecordingEmbedder()
    now = [100.0]
    embedder = CachedEmbedder(
        delegate,
        max_entries=2,
        ttl_seconds=10,
        monotonic=lambda: now[0],
    )

    assert embedder.embed(["same query"]) == [[10.0]]
    assert embedder.embed(["same query"]) == [[10.0]]
    assert delegate.calls == [["same query"]]
    assert embedder.hits == 1
    assert embedder.misses == 1

    now[0] = 111.0
    assert embedder.embed(["same query"]) == [[10.0]]
    assert delegate.calls == [["same query"], ["same query"]]
    assert embedder.misses == 2
    assert embedder.cache_info() == {
        "entries": 1,
        "hits": 1,
        "misses": 2,
        "max_entries": 2,
        "ttl_seconds": 10,
    }


def test_cached_embedder_evicts_least_recently_used_entry():
    delegate = RecordingEmbedder()
    embedder = CachedEmbedder(delegate, max_entries=2, ttl_seconds=60)

    embedder.embed(["a"])
    embedder.embed(["bb"])
    embedder.embed(["a"])
    embedder.embed(["ccc"])
    embedder.embed(["bb"])

    assert delegate.calls == [["a"], ["bb"], ["ccc"], ["bb"]]
