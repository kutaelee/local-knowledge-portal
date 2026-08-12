from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MODEL_ID = "Peutlefaire/Qwen3.6-27B-NVFP4"
SERVED_MODEL = "qwen3.6-27b-nvfp4"
KERNEL_LINE = "Using FlashInferCutlassNvFp4LinearKernel for NVFP4 GEMM"
FALLBACK_LINE = "Using CutlassNvFp4LinearKernel for NVFP4 GEMM"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def windows_to_wsl(path: Path) -> str:
    value = str(path.resolve())
    if len(value) < 3 or value[1:3] != ":\\":
        raise ValueError(f"expected a Windows drive path: {value}")
    drive = value[0].lower()
    return f"/mnt/{drive}/{value[3:].replace(chr(92), '/')}"


def port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def wait_for_health(
    process: subprocess.Popen[Any],
    port: int,
    timeout: int,
    server_log: Path,
) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Windows NVFP4 server exited before readiness: {process.returncode}"
            )
        if server_log.exists():
            log_tail = server_log.read_text(encoding="utf-8", errors="replace")[-32_768:]
            if "EngineCore failed to start" in log_tail:
                raise RuntimeError("Windows NVFP4 EngineCore failed before readiness")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(2)
    raise TimeoutError(f"Windows NVFP4 server did not become healthy: {url}")


def strict_json_probe(port: int) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "ok": {"type": "boolean"},
            "model": {"type": "string"},
        },
        "required": ["ok", "model"],
        "additionalProperties": False,
    }
    payload = {
        "model": SERVED_MODEL,
        "messages": [
            {
                "role": "user",
                "content": (
                    "Return a JSON object with ok=true and "
                    f"model={SERVED_MODEL}. Do not add other fields."
                ),
            }
        ],
        "temperature": 0,
        "max_tokens": 128,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "repository_analysis_probe",
                "strict": True,
                "schema": schema,
            },
        },
    }
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        body = json.loads(response.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    decoded = json.loads(content)
    if decoded != {"ok": True, "model": SERVED_MODEL}:
        raise RuntimeError(f"strict JSON probe returned unexpected data: {decoded}")
    return {
        "passed": True,
        "finish_reason": body["choices"][0].get("finish_reason"),
        "usage": body.get("usage", {}),
    }


def stop_server(process: subprocess.Popen[Any], port: int) -> dict[str, Any]:
    if process.poll() is None:
        try:
            process.send_signal(signal.CTRL_BREAK_EVENT)
            process.wait(timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
    deadline = time.monotonic() + 30
    while port_is_open(port) and time.monotonic() < deadline:
        time.sleep(1)
    return {
        "process_exit_code": process.poll(),
        "port_closed": not port_is_open(port),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run one checkpointed IndigoESB analysis or support evaluation "
            "with the qualified Windows-native Qwen3.6 NVFP4 server."
        )
    )
    parser.add_argument(
        "component",
        choices=("esb", "imc", "agent", "support-evaluation"),
    )
    parser.add_argument("--runtime", type=Path, default=Path(r"E:\AI\Apps\Qwen36"))
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=Path(
            r"E:\AI\Models\HuggingFace\hub"
            r"\models--Peutlefaire--Qwen3.6-27B-NVFP4"
            r"\snapshots\71d46214a7ef0f1205dd536f63203e5b51b415ee"
        ),
    )
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--max-model-len", type=int, default=16000)
    parser.add_argument("--max-output", type=int, default=2048)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.90)
    parser.add_argument("--kv-cache-memory-bytes", type=int, default=2_000_000_000)
    parser.add_argument("--startup-timeout", type=int, default=1800)
    parser.add_argument("--cache-root", type=Path, default=Path(r"E:\AI\Temp\v36fi180"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path(r"E:\AI\Temp\local-knowledge-portal"),
    )
    parser.add_argument(
        "--linux-repo",
        default="/home/kutae/src/local-knowledge-portal",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    started_at = utcnow()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_root / (f"qwen36-nvfp4-{args.component}-{stamp}")
    output_dir.mkdir(parents=True, exist_ok=False)
    summary_path = output_dir / "run-summary.json"
    wrapper_log = output_dir / "launcher.log"
    server_log = output_dir / f"vllm_server.{args.port}.log"
    snapshot = args.runtime / "snapshots" / "start_5090_nvfp4_loopback_safe.py"
    python = args.runtime / "python" / "python.exe"
    summary: dict[str, Any] = {
        "schema_version": 1,
        "started_at": started_at,
        "component": args.component,
        "model": MODEL_ID,
        "served_model": SERVED_MODEL,
        "configuration": {
            "max_model_len": args.max_model_len,
            "max_output": args.max_output,
            "gpu_memory_utilization": args.gpu_memory_utilization,
            "kv_cache_memory_bytes": args.kv_cache_memory_bytes,
            "kv_cache_dtype": "fp8_e4m3",
            "mtp_speculative_tokens": 6,
            "quantization": "compressed-tensors NVFP4",
            "checkpointing": "per_terminal_task",
        },
        "output_dir": str(output_dir),
        "server_log": str(server_log),
        "status": "STARTING",
    }
    server: subprocess.Popen[Any] | None = None
    launcher_stream = None
    analysis_exit_code: int | None = None
    cleanup: dict[str, Any] = {}
    exit_code = 1

    def write_summary() -> None:
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def interrupted(signum: int, _frame: Any) -> None:
        raise InterruptedError(f"received signal {signum}")

    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        value = getattr(signal, name, None)
        if value is not None:
            signal.signal(value, interrupted)

    try:
        for required in (python, snapshot, args.model_dir):
            if not required.exists():
                raise FileNotFoundError(required)
        if port_is_open(args.port):
            raise RuntimeError(f"port {args.port} is already in use; no process was stopped")
        env = os.environ.copy()
        env.update(
            {
                "VLLM_NVFP4_MODEL_DIR": str(args.model_dir),
                "VLLM_NVFP4_MAX_MODEL_LEN": str(args.max_model_len),
                "VLLM_NVFP4_GPU_MEMORY_UTILIZATION": str(args.gpu_memory_utilization),
                "VLLM_NVFP4_KV_CACHE_MEMORY_BYTES": str(args.kv_cache_memory_bytes),
                "VLLM_CACHE_ROOT": str(args.cache_root),
                "VLLM_WINDOWS_LOGS": str(output_dir),
                "VLLM_HAS_FLASHINFER_CUBIN": "1",
                "VLLM_NVFP4_GEMM_BACKEND": "flashinfer-cutlass",
                "HF_HUB_OFFLINE": "1",
                "TRANSFORMERS_OFFLINE": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        launcher_stream = wrapper_log.open("w", encoding="utf-8", buffering=1)
        server = subprocess.Popen(
            [str(python), str(snapshot)],
            cwd=str(args.runtime),
            env=env,
            stdout=launcher_stream,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        summary["server_wrapper_pid"] = server.pid
        summary["status"] = "WAITING_FOR_SERVER"
        write_summary()
        wait_for_health(server, args.port, args.startup_timeout, server_log)
        log_text = server_log.read_text(encoding="utf-8", errors="replace")
        if KERNEL_LINE not in log_text:
            raise RuntimeError("FlashInfer NVFP4 kernel selection was not logged")
        if FALLBACK_LINE in log_text:
            raise RuntimeError("plain Cutlass NVFP4 fallback was logged")
        summary["kernel"] = {
            "selected": "FlashInferCutlassNvFp4LinearKernel",
            "plain_cutlass_fallback": False,
        }
        summary["strict_json_probe"] = strict_json_probe(args.port)
        summary["status"] = "ANALYZING"
        write_summary()
        wsl_server_log = windows_to_wsl(server_log)
        command = [
            "wsl.exe",
            "-d",
            "Ubuntu",
            "--cd",
            args.linux_repo,
            "--",
            "env",
            "LKP_REPOSITORY_EXTERNAL_VLLM=true",
            f"LKP_REPOSITORY_EXTERNAL_VLLM_LOG={wsl_server_log}",
            f"LKP_REPOSITORY_VLLM_PORT={args.port}",
            f"LKP_REPOSITORY_VLLM_MAX_MODEL_LEN={args.max_model_len}",
            f"LKP_REPOSITORY_MODEL_MAX_OUTPUT={args.max_output}",
            f"LKP_REPOSITORY_MODEL_NAME={SERVED_MODEL}",
            "LKP_REPOSITORY_MODEL_QUANTIZATION=NVFP4",
            "bash",
        ]
        if args.component == "support-evaluation":
            command.append("scripts/evaluate-indigo-support.sh")
        else:
            command.extend(
                [
                    "scripts/analyze-indigo-component.sh",
                    args.component,
                ]
            )
        completed = subprocess.run(command, cwd=str(args.runtime), check=False)
        analysis_exit_code = completed.returncode
        if analysis_exit_code != 0:
            raise RuntimeError(f"{args.component} operation exited {analysis_exit_code}")
        summary["status"] = "COMPLETED"
        exit_code = 0
    except BaseException as exc:
        summary["status"] = "FAILED"
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)
        print(f"[orchestrator] {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        summary["analysis_exit_code"] = analysis_exit_code
        if server is not None:
            cleanup = stop_server(server, args.port)
        else:
            cleanup = {"process_exit_code": None, "port_closed": True}
        if launcher_stream is not None:
            launcher_stream.close()
        summary["cleanup"] = cleanup
        summary["finished_at"] = utcnow()
        write_summary()
        if not cleanup.get("port_closed", False):
            summary["status"] = "FAILED_CLEANUP"
            exit_code = 1
            print(
                f"[orchestrator] port {args.port} remained open after cleanup",
                file=sys.stderr,
            )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
