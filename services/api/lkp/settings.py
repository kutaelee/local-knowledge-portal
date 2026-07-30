import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LKP_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://lkp:lkp@127.0.0.1:55432/lkp"
    source_roots_config: Path = Path("config/source-roots.yaml")
    settings_config: Path = Path("config/settings.yaml")
    runtime_dir: Path = Path("runtime")
    ingest_dir: Path = Path("runtime/ingest")
    vault_dir: Path = Path("runtime/vault")
    backup_dir: Path = Path("runtime/backups")
    codex_home: Path = Field(default_factory=lambda: Path.home() / ".codex")
    codex_additional_homes: str = ""
    codex_capture_poll_seconds: float = 5.0
    api_host: str = "127.0.0.1"
    api_port: int = 8010
    cors_origins: str = "http://127.0.0.1:3010,http://localhost:3010"
    gpu_scheduler_base_url: str = "http://host.docker.internal:8790"
    gpu_scheduler_timeout_seconds: float = Field(default=2.0, ge=0.2, le=10)
    gpu_scheduler_control_token: str = Field(default="", repr=False)
    service_manager_base_url: str = "http://host.docker.internal:8791"
    service_manager_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30)
    service_manager_action_timeout_seconds: float = Field(
        default=150.0,
        ge=30,
        le=300,
    )
    service_manager_token: str = Field(default="", repr=False)
    comfyui_bridge_health_url: str = "http://host.docker.internal:8188/gpuq_bridge/health"
    gpu_embedding_reaper_path: Path = Path("/data/runtime/gpu-embedding-reaper.json")
    gpu_embedding_reaper_health_enabled: bool = False
    docker_inventory_path: Path = Path("/data/runtime/docker-services.json")
    docker_inventory_stale_seconds: int = Field(default=90, ge=30, le=3600)
    ollama_base_url: str = "http://127.0.0.1:11434"
    embedding_provider: str = "ollama"
    embedding_model: str = "qwen3-embedding:0.6b"
    embedding_dimension: int = 1024
    embedding_revision: str = "ollama-qwen3-embedding-0.6b-d1024-v1"
    embedding_model_digest: str = "unresolved"
    embedding_batch_size: int = Field(default=2, ge=1, le=32)
    embedding_batch_cooldown_seconds: float = Field(default=0.5, ge=0, le=60)
    embedding_request_timeout_seconds: float = Field(default=90, ge=5, le=300)
    # Interactive and ordinary worker requests must release VRAM immediately.
    # GPU-scheduled batch services override this briefly and unload in finally.
    embedding_keep_alive: str = "0"
    embedding_input_max_chars: int = Field(default=2400, ge=256, le=100_000)
    embedding_timeout_circuit_threshold: int = Field(default=3, ge=1, le=100)
    embedding_timeout_circuit_window_seconds: int = Field(default=3600, ge=60, le=86_400)
    embedding_timeout_circuit_bypass: bool = False
    # Normal CPU inference can be suspended explicitly while a gpuq-managed
    # reindex repairs deferred vectors. This does not affect the one-shot
    # reindex service, which sets the bypass flag in its isolated environment.
    embedding_runtime_mode: Literal["enabled", "deferred_gpu_recovery"] = "enabled"
    embedding_max_chunks_per_document: int = Field(default=128, ge=1, le=4096)
    embedding_max_chars_per_document: int = Field(default=250_000, ge=1_000, le=100_000_000)
    semantic_high_confidence_similarity: float = Field(default=0.6, ge=-1, le=1)
    query_embedding_cache_size: int = Field(default=512, ge=1, le=100_000)
    query_embedding_cache_ttl_seconds: int = Field(default=86_400, ge=60, le=2_592_000)
    query_embedding_timeout_seconds: int = Field(default=5, ge=1, le=120)
    query_embedding_prewarm: bool = False
    search_statement_timeout_ms: int = Field(default=5_000, ge=100, le=120_000)
    repository_embedding_mode: Literal["docs_only", "code_and_docs", "lexical_only"] = "docs_only"
    generation_provider: str = "disabled"
    generation_base_url: str = "http://127.0.0.1:11434"
    generation_model: str = ""
    generation_model_digest: str = "unresolved"
    generation_timeout_seconds: int = 120
    generation_max_input_chars: int = 40000
    generation_prompt_version: str = "evidence-blog-v10-reported-provenance"
    generation_fallback_models: str = "gemma4:12b,qwen3:14b"
    generation_temperature: float = Field(default=0, ge=0, le=2)
    generation_context_window: int = Field(default=16_384, ge=2_048, le=262_144)
    generation_keep_alive: str = "2m"
    project_article_enabled: bool = True
    project_article_prompt_version: str = (
        "project-article-v3-hierarchical-source-manifest"
    )
    project_article_batch_chars: int = Field(
        default=24_000, ge=4_000, le=100_000
    )
    project_article_min_group_coverage: float = Field(
        default=0.45, ge=0.25, le=1.0
    )
    project_article_max_projects_per_run: int = Field(
        default=20, ge=1, le=500
    )
    knowledge_curation_enabled: bool = False
    knowledge_curation_auto_publish: bool = True
    knowledge_dedup_similarity_threshold: float = Field(default=0.88, ge=0.5, le=0.999)
    knowledge_dedup_max_candidates_per_run: int = Field(default=50, ge=1, le=500)
    knowledge_curation_poll_seconds: int = Field(default=60, ge=10, le=3600)
    knowledge_curation_gpu_min_free_mb: int = Field(default=12_288, ge=1_024, le=131_072)
    knowledge_curation_gpu_max_utilization: int = Field(default=15, ge=0, le=100)
    knowledge_curation_gpu_max_temperature: int = Field(default=70, ge=20, le=100)
    knowledge_curation_busy_retry_base_seconds: int = Field(default=900, ge=60, le=86_400)
    knowledge_curation_busy_retry_max_seconds: int = Field(default=14_400, ge=60, le=604_800)
    knowledge_curation_busy_max_checks: int = Field(default=6, ge=1, le=100)
    knowledge_curation_exhausted_cooldown_seconds: int = Field(
        default=86_400, ge=3_600, le=2_592_000
    )
    knowledge_curation_min_article_chars: int = Field(default=0, ge=0, le=5000)
    knowledge_curation_max_article_chars: int = Field(default=10_000, ge=1_000, le=50_000)
    hook_spool_dir: Path = Path("runtime/ingest/codex-spool")
    local_llm_spool_dir: Path = Path("runtime/ingest/local-llm-spool")
    hook_spool_fallback_dir: Path = Field(
        default_factory=lambda: (
            Path(os.getenv("LOCALAPPDATA", str(Path.home())))
            / "LocalKnowledgePortal"
            / "spool-fallback"
        )
    )
    codex_sessions_dir: Path = Path("/codex-sessions")
    hook_collector_poll_seconds: float = 2.0
    hook_claim_stale_seconds: int = 60
    knowledge_auto_publish: bool = False
    knowledge_content_language: Literal["ko", "en"] = "ko"
    developer_feed_enabled: bool = True
    developer_feed_interval_minutes: int = Field(default=180, ge=15, le=1440)
    developer_feed_daily_hour: int = Field(default=18, ge=0, le=23)
    developer_feed_daily_summary_lag_days: int = Field(default=0, ge=0, le=7)
    developer_feed_timezone: str = "Asia/Seoul"
    developer_feed_initial_lookback_hours: int = Field(default=72, ge=1, le=720)
    developer_feed_max_sources_per_run: int = Field(default=12, ge=1, le=100)
    developer_feed_max_input_chars: int = Field(default=24_000, ge=4_000, le=100_000)
    developer_feed_persona_version: str = "workstation-developer-v7-session-notes"
    developer_feed_prompt_version: str = "developer-feed-v7-no-principles"
    developer_feed_model: str = "gemma4:12b"
    developer_feed_model_digest: str = (
        "4eb23ef187e2c5462566d6a1d3bbbc2f1346d0b4327cbb66d58fffbcc9b2b05c"
    )
    developer_feed_timeout_seconds: int = Field(default=600, ge=30, le=3600)
    developer_feed_context_window: int = Field(default=16_384, ge=2_048, le=262_144)
    developer_feed_temperature: float = Field(default=0.65, ge=0, le=1)
    activity_detail_retention_days: int = Field(default=30, ge=1, le=3650)
    terminal_job_detail_retention_days: int = Field(default=90, ge=1, le=3650)
    ingest_event_detail_retention_days: int = Field(default=90, ge=1, le=3650)
    activity_retention_check_seconds: int = Field(default=3600, ge=60, le=86400)
    mount_guard_paths: str = ""
    mount_guard_nonempty_dirs: str = ""
    knowledge_transcript_tail_bytes: int = Field(default=8_000_000, ge=1_000_000, le=32_000_000)
    pipeline_version: str = "1.2.0"
    parser_version: str = "markdown-it-py-4"
    chunker_version: str = "lkp-heading-symbol-v1"
    max_file_bytes: int = 10 * 1024 * 1024
    lease_seconds: int = 120
    max_attempts: int = 5
    heartbeat_seconds: int = 10
    stale_after_seconds: int = 45
    worker_job_cooldown_seconds: float = Field(default=1.0, ge=0, le=300)
    worker_id: str = Field(default="worker-service:primary", min_length=3, max_length=200)
    worker_burst_jobs: int = Field(default=20, ge=1, le=10000)
    worker_burst_cooldown_seconds: float = Field(default=15.0, ge=0, le=3600)
    worker_lexical_job_cooldown_seconds: float = Field(default=0.05, ge=0, le=300)
    worker_lexical_burst_jobs: int = Field(default=200, ge=1, le=10000)
    worker_lexical_burst_cooldown_seconds: float = Field(default=2.0, ge=0, le=3600)
    worker_pause_file: Path = Path("runtime/embedding.pause")
    worker_pause_poll_seconds: float = Field(default=5.0, ge=0.5, le=300)
    reconciliation_seconds: int = 300
    watch_debounce_ms: int = Field(default=750, ge=0)
    watch_force_polling: bool = False
    watch_polling_roots: str = ""
    watch_poll_delay_ms: int = Field(default=2000, ge=1000)
    watch_cpu_warning_percent: float = Field(default=50.0, ge=1.0)
    watch_cpu_warning_samples: int = Field(default=3, ge=1)
    watch_cpu_grace_seconds: int = Field(default=60, ge=0)
    file_stability_seconds: float = 0.5
    allowed_source_roots: list[Path] = Field(default_factory=list)

    @field_validator("api_host")
    @classmethod
    def localhost_guard(cls, value: str) -> str:
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("non-local API bind requires an authentication implementation")
        return value

    @field_validator("embedding_keep_alive")
    @classmethod
    def bounded_embedding_keep_alive(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if normalized not in {"0", "0s", "30s", "1m", "2m"}:
            raise ValueError("embedding keep-alive must be zero or at most 2m")
        return normalized

    @field_validator("ollama_base_url", "generation_base_url")
    @classmethod
    def local_model_guard(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "host.docker.internal",
            "ollama",
            "ollama-generation",
            "ollama-embedding-batch",
        }:
            raise ValueError(
                "model providers must use localhost/loopback or an approved private "
                "Docker Ollama service"
            )
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("model provider base URL cannot contain a path, query, or fragment")
        return value.rstrip("/")

    @field_validator("gpu_scheduler_base_url")
    @classmethod
    def local_gpu_scheduler_guard(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "host.docker.internal",
        }:
            raise ValueError(
                "GPU scheduler must use loopback or Docker Desktop's host.docker.internal"
            )
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("GPU scheduler base URL cannot contain a path, query, or fragment")
        return value.rstrip("/")

    @field_validator("service_manager_base_url")
    @classmethod
    def local_service_manager_guard(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "host.docker.internal",
        }:
            raise ValueError(
                "service manager must use loopback or Docker Desktop's host boundary"
            )
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("service manager base URL cannot contain a path")
        return value.rstrip("/")

    @field_validator("comfyui_bridge_health_url")
    @classmethod
    def local_comfyui_bridge_guard(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "host.docker.internal",
        }:
            raise ValueError("ComfyUI bridge must use a local host boundary")
        if parsed.path != "/gpuq_bridge/health" or parsed.query or parsed.fragment:
            raise ValueError("ComfyUI bridge health URL must use /gpuq_bridge/health only")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def codex_home_list(self) -> list[Path]:
        additional = [
            Path(item.strip()) for item in self.codex_additional_homes.split(";") if item.strip()
        ]
        return [self.codex_home, *additional]

    @property
    def hook_spool_roots(self) -> list[Path]:
        return [
            self.hook_spool_dir,
            self.local_llm_spool_dir,
            self.hook_spool_fallback_dir,
        ]

    @property
    def watch_polling_root_set(self) -> set[str]:
        return {
            str(Path(item.strip()).resolve(strict=False))
            for item in self.watch_polling_roots.replace(";", ",").split(",")
            if item.strip()
        }

    @property
    def mount_guard_path_list(self) -> list[Path]:
        return [
            Path(item.strip())
            for item in self.mount_guard_paths.replace(",", ";").split(";")
            if item.strip()
        ]

    @property
    def mount_guard_nonempty_dir_list(self) -> list[Path]:
        return [
            Path(item.strip())
            for item in self.mount_guard_nonempty_dirs.replace(",", ";").split(";")
            if item.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
