from pathlib import Path

from lkp_indexer.file_safety import source_file_rejection_reason


def test_source_file_safety_rejects_binary_oversized_and_invalid_utf8(
    tmp_path: Path,
) -> None:
    text = tmp_path / "safe.log"
    text.write_text("정상 로그\n", encoding="utf-8")
    assert source_file_rejection_reason(text, 1024) is None

    binary = tmp_path / "leveldb.log"
    binary.write_bytes(b"record\x00payload")
    assert source_file_rejection_reason(binary, 1024) == "binary"

    invalid = tmp_path / "legacy.txt"
    invalid.write_bytes(b"\xff\xfelegacy")
    assert source_file_rejection_reason(invalid, 1024) == "invalid_utf8"

    oversized = tmp_path / "large.json"
    oversized.write_bytes(b"x" * 1025)
    assert source_file_rejection_reason(oversized, 1024) == "oversized"
