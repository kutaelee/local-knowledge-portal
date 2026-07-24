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
    ollama_base_url: str = "http://127.0.0.1:11434"
    embedding_provider: str = "ollama"
    embedding_model: str = "qwen3-embedding:0.6b"
    embedding_dimension: int = 1024
    embedding_revision: str = "ollama-qwen3-embedding-0.6b-d1024-v1"
    embedding_model_digest: str = "unresolved"
    embedding_batch_size: int = Field(default=2, ge=1, le=32)
    embedding_batch_cooldown_seconds: float = Field(default=0.5, ge=0, le=60)
    embedding_max_chunks_per_document: int = Field(default=128, ge=1, le=4096)
    embedding_max_chars_per_document: int = Field(
        default=250_000, ge=1_000, le=100_000_000
    )
    semantic_high_confidence_similarity: float = Field(
        default=0.6, ge=-1, le=1
    )
    query_embedding_cache_size: int = Field(default=512, ge=1, le=100_000)
    query_embedding_cache_ttl_seconds: int = Field(
        default=86_400, ge=60, le=2_592_000
    )
    query_embedding_timeout_seconds: int = Field(default=30, ge=1, le=120)
    query_embedding_prewarm: bool = False
    search_statement_timeout_ms: int = Field(default=5_000, ge=100, le=120_000)
    repository_embedding_mode: Literal[
        "docs_only", "code_and_docs", "lexical_only"
    ] = "docs_only"
    generation_provider: str = "disabled"
    generation_base_url: str = "http://127.0.0.1:11434"
    generation_model: str = ""
    generation_model_digest: str = "unresolved"
    generation_timeout_seconds: int = 120
    generation_max_input_chars: int = 40000
    hook_spool_dir: Path = Path("runtime/ingest/codex-spool")
    hook_spool_fallback_dir: Path = Field(
        default_factory=lambda: Path(os.getenv("LOCALAPPDATA", str(Path.home())))
        / "LocalKnowledgePortal"
        / "spool-fallback"
    )
    codex_sessions_dir: Path = Path("/codex-sessions")
    hook_collector_poll_seconds: float = 2.0
    hook_claim_stale_seconds: int = 60
    knowledge_auto_publish: bool = True
    knowledge_transcript_tail_bytes: int = Field(
        default=8_000_000, ge=1_000_000, le=32_000_000
    )
    pipeline_version: str = "1.2.0"
    parser_version: str = "markdown-it-py-4"
    chunker_version: str = "lkp-heading-symbol-v1"
    max_file_bytes: int = 10 * 1024 * 1024
    lease_seconds: int = 120
    max_attempts: int = 5
    heartbeat_seconds: int = 10
    stale_after_seconds: int = 45
    worker_job_cooldown_seconds: float = Field(default=1.0, ge=0, le=300)
    worker_burst_jobs: int = Field(default=20, ge=1, le=10000)
    worker_burst_cooldown_seconds: float = Field(default=15.0, ge=0, le=3600)
    worker_lexical_job_cooldown_seconds: float = Field(default=0.05, ge=0, le=300)
    worker_lexical_burst_jobs: int = Field(default=200, ge=1, le=10000)
    worker_lexical_burst_cooldown_seconds: float = Field(
        default=2.0, ge=0, le=3600
    )
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

    @field_validator("ollama_base_url", "generation_base_url")
    @classmethod
    def local_model_guard(cls, value: str) -> str:
        if urlparse(value).hostname not in {"127.0.0.1", "localhost", "::1", "ollama"}:
            raise ValueError(
                "model providers must use localhost/loopback or the private Docker service "
                "'ollama'"
            )
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def codex_home_list(self) -> list[Path]:
        additional = [
            Path(item.strip())
            for item in self.codex_additional_homes.split(";")
            if item.strip()
        ]
        return [self.codex_home, *additional]

    @property
    def hook_spool_roots(self) -> list[Path]:
        return [self.hook_spool_dir, self.hook_spool_fallback_dir]

    @property
    def watch_polling_root_set(self) -> set[str]:
        return {
            str(Path(item.strip()).resolve(strict=False))
            for item in self.watch_polling_roots.replace(";", ",").split(",")
            if item.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
