#!/usr/bin/env python3
"""Launch vLLM with a narrow workaround for local GGUF models using MTP."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _option_value(argv: list[str], name: str) -> str | None:
    for index, value in enumerate(argv):
        if value == name and index + 1 < len(argv):
            return argv[index + 1]
        if value.startswith(f"{name}="):
            return value.split("=", 1)[1]
    return None


def _validated_local_gguf_mtp(argv: list[str]) -> Path:
    if len(argv) < 3 or argv[1] != "serve":
        raise SystemExit("this launcher only supports: serve <local-model.gguf>")

    model = Path(argv[2]).resolve()
    if model.suffix.lower() != ".gguf" or not model.is_file():
        raise SystemExit("the served model must be an existing local .gguf file")

    config_path_value = _option_value(argv, "--hf-config-path")
    if not config_path_value:
        raise SystemExit("--hf-config-path is required for local GGUF MTP")
    config_path = Path(config_path_value).resolve()
    if not (config_path / "config.json").is_file():
        raise SystemExit("--hf-config-path must contain config.json")

    speculative_value = _option_value(argv, "--speculative-config")
    try:
        speculative = json.loads(speculative_value or "")
    except json.JSONDecodeError as error:
        raise SystemExit("--speculative-config must be valid JSON") from error
    if speculative != {"method": "mtp", "num_speculative_tokens": 1}:
        raise SystemExit("only MTP-1 is allowed by this launcher")

    if "--no-enable-prefix-caching" not in argv:
        raise SystemExit("prefix caching must be disabled for this workload")
    if _option_value(argv, "--load-format") != "gguf":
        raise SystemExit("--load-format gguf is required")
    return model


def main() -> None:
    expected_model = _validated_local_gguf_mtp(sys.argv)

    import vllm.engine.arg_utils as arg_utils
    from vllm.model_executor.models import ModelRegistry

    # vLLM 0.25.1 contains the Qwen3.5/3.6 text implementation but only
    # registers the multimodal wrapper. Register the existing text classes
    # explicitly so --language-model-only does not fall back to the vision
    # architecture. Qwen3.6 uses the same qwen3_5 implementation names.
    ModelRegistry.register_model(
        "Qwen3_5ForCausalLM",
        "vllm.model_executor.models.qwen3_5:Qwen3_5ForCausalLM",
    )
    ModelRegistry.register_model(
        "Qwen3_5MoeForCausalLM",
        "vllm.model_executor.models.qwen3_5:Qwen3_5MoeForCausalLM",
    )

    original = arg_utils.maybe_override_with_speculators

    def keep_explicit_mtp_for_local_gguf(
        model: str,
        tokenizer: str | None,
        trust_remote_code: bool,
        revision: str | None = None,
        vllm_speculative_config: dict[str, Any] | None = None,
        hf_token: bool | str | None = None,
        **kwargs: Any,
    ) -> tuple[str, str | None, dict[str, Any] | None]:
        if (
            Path(model).resolve() == expected_model
            and vllm_speculative_config
            == {"method": "mtp", "num_speculative_tokens": 1}
        ):
            # vLLM 0.25.1 otherwise tries to decode the binary GGUF path as
            # config.json before its GGUF config parser can use hf-config-path.
            return model, tokenizer, vllm_speculative_config
        return original(
            model=model,
            tokenizer=tokenizer,
            trust_remote_code=trust_remote_code,
            revision=revision,
            vllm_speculative_config=vllm_speculative_config,
            hf_token=hf_token,
            **kwargs,
        )

    arg_utils.maybe_override_with_speculators = keep_explicit_mtp_for_local_gguf

    from vllm.entrypoints.cli.main import main as vllm_main

    vllm_main()


if __name__ == "__main__":
    main()
