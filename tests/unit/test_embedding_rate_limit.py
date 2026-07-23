from lkp_indexer.embedding import RateLimitedEmbedder


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
