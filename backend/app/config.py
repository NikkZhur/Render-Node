"""Environment-backed application configuration."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pydantic import AnyHttpUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Validated settings loaded from ``RENDER_NODE_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="RENDER_NODE_",
        case_sensitive=False,
        extra="ignore",
        enable_decoding=False,
    )

    env: Environment = Environment.DEVELOPMENT
    app_name: str = "Render Node"
    app_version: str = "0.1.0"
    api_prefix: str = "/api/v1"
    workspace: Path = Path("/workspace")
    database_url: str = ""
    max_upload_gb: float = Field(default=20, gt=0, le=1024)
    max_zip_files: int = Field(default=10_000, ge=1, le=100_000)
    max_zip_extracted_gb: float = Field(default=50, gt=0, le=2048)
    max_blender_archive_gb: float = Field(default=2, gt=0, le=20)
    max_blender_extracted_gb: float = Field(default=8, gt=0, le=100)
    max_blender_archive_files: int = Field(default=100_000, ge=1, le=500_000)
    blender_catalog_ttl_seconds: int = Field(default=3600, ge=60, le=86_400)
    blender_download_timeout_seconds: int = Field(default=3600, ge=30, le=86_400)
    render_scheduler_enabled: bool = True
    allow_unsandboxed_runner: bool = False
    blender_executable_override: Path | None = None
    render_timeout_seconds: int = Field(default=21_600, ge=1, le=604_800)
    render_terminate_grace_seconds: float = Field(default=5, ge=0.1, le=60)
    render_scheduler_poll_seconds: float = Field(default=0.5, ge=0.05, le=30)
    max_render_output_gb: float = Field(default=20, gt=0, le=2048)
    max_render_log_mb: int = Field(default=100, ge=1, le=4096)
    worker_memory_gb: float = Field(default=16, gt=0, le=2048)
    worker_pids_limit: int = Field(default=256, ge=16, le=65_536)
    preview_max_width: int = Field(default=1280, ge=64, le=8192)
    preview_max_height: int = Field(default=1280, ge=64, le=8192)
    preview_max_megapixels: int = Field(default=100, ge=1, le=500)
    event_queue_size: int = Field(default=256, ge=16, le=4096)
    metrics_interval_seconds: float = Field(default=1, ge=1, le=60)
    low_space_percent: float = Field(default=10, ge=1, le=99)
    low_space_gb: float = Field(default=50, ge=1, le=10_000)
    allowed_origins: list[AnyHttpUrl] = Field(
        default_factory=lambda: [
            AnyHttpUrl("http://localhost:5173"),
            AnyHttpUrl("http://127.0.0.1:5173"),
        ]
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_allowed_origins(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        raw_value = value.strip()
        if not raw_value:
            return []
        if raw_value.startswith("["):
            return json.loads(raw_value)
        return [origin.strip() for origin in raw_value.split(",") if origin.strip()]

    @field_validator("api_prefix")
    @classmethod
    def validate_api_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/"):
            raise ValueError("api_prefix must start with '/' and must not end with '/'")
        return value

    @model_validator(mode="after")
    def validate_runtime_settings(self) -> Self:
        self.workspace = self.workspace.expanduser().resolve()
        if not self.database_url:
            database_path = self.workspace / "database" / "render-node.sqlite3"
            self.database_url = f"sqlite+aiosqlite:///{database_path}"

        database = make_url(self.database_url)
        if database.drivername != "sqlite+aiosqlite":
            raise ValueError("database_url must use the sqlite+aiosqlite driver")
        if database.database != ":memory:":
            if database.database is None or not Path(database.database).is_absolute():
                raise ValueError("database_url must reference an absolute SQLite path")

        if any(str(origin) == "*" for origin in self.allowed_origins):
            raise ValueError("allowed_origins cannot contain a wildcard")
        if self.blender_executable_override is not None:
            self.blender_executable_override = (
                self.blender_executable_override.expanduser().resolve()
            )
            if self.env is Environment.PRODUCTION:
                raise ValueError("blender_executable_override is forbidden in production")
        if self.env is Environment.PRODUCTION and self.allow_unsandboxed_runner:
            raise ValueError("allow_unsandboxed_runner is forbidden in production")
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.allowed_origins]

    @property
    def database_path(self) -> Path | None:
        database = make_url(self.database_url).database
        if database is None or database == ":memory:":
            return None
        return Path(database)

    @property
    def jobs_root(self) -> Path:
        return self.workspace / "jobs"

    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_gb * 1024**3)

    @property
    def max_zip_extracted_bytes(self) -> int:
        return int(self.max_zip_extracted_gb * 1024**3)

    @property
    def blender_root(self) -> Path:
        return self.workspace / "blender"

    @property
    def blender_versions_root(self) -> Path:
        return self.blender_root / "versions"

    @property
    def blender_downloads_root(self) -> Path:
        return self.blender_root / "downloads"

    @property
    def blender_quarantine_root(self) -> Path:
        return self.blender_root / "quarantine"

    @property
    def max_blender_archive_bytes(self) -> int:
        return int(self.max_blender_archive_gb * 1024**3)

    @property
    def max_blender_extracted_bytes(self) -> int:
        return int(self.max_blender_extracted_gb * 1024**3)

    @property
    def max_render_output_bytes(self) -> int:
        return int(self.max_render_output_gb * 1024**3)

    @property
    def max_render_log_bytes(self) -> int:
        return self.max_render_log_mb * 1024**2

    @property
    def worker_memory_bytes(self) -> int:
        return int(self.worker_memory_gb * 1024**3)
