from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "vllm-gguf-mtp-launcher.py"
SPEC = importlib.util.spec_from_file_location("vllm_gguf_mtp_launcher", SCRIPT)
assert SPEC and SPEC.loader
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)


def _argv(tmp_path: Path) -> list[str]:
    model = tmp_path / "model.gguf"
    model.touch()
    config = tmp_path / "config"
    config.mkdir()
    (config / "config.json").write_text("{}", encoding="utf-8")
    return [
        str(SCRIPT),
        "serve",
        str(model),
        "--hf-config-path",
        str(config),
        "--load-format",
        "gguf",
        "--speculative-config",
        '{"method":"mtp","num_speculative_tokens":1}',
        "--no-enable-prefix-caching",
    ]


def test_accepts_only_bounded_local_gguf_mtp_shape(tmp_path: Path) -> None:
    argv = _argv(tmp_path)

    assert LAUNCHER._validated_local_gguf_mtp(argv) == Path(argv[2]).resolve()


def test_rejects_prefix_caching(tmp_path: Path) -> None:
    argv = _argv(tmp_path)
    argv.remove("--no-enable-prefix-caching")

    with pytest.raises(SystemExit, match="prefix caching must be disabled"):
        LAUNCHER._validated_local_gguf_mtp(argv)
