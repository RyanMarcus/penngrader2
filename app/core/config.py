from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "PennGrader 2"
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    pg_dsn: str = "postgresql://postgres:postgres@localhost:5432/penngrader2"

    api_key_student: str = "student-dev-key"
    api_key_ta: str = "ta-dev-key"
    api_key_instructor: str = "instructor-dev-key"

    worker_id: str = "worker-1"
    worker_concurrency: int = 5
    worker_poll_interval_seconds: float = 1.0
    queue_update_interval_seconds: float = 5.0

    submission_rate_limit_seconds: int = 30
    grader_timeout_seconds: int = 600
    grader_runtime_image: str = "penngrader2-grader-runtime:latest"
    grader_memory_limit: str = "512m"
    grader_cpus: str = "1.0"

    allowed_imports_file: str = "config/allowed_imports.toml"

    event_poll_interval_seconds: float = 1.0
    event_heartbeat_seconds: float = 15.0

    @property
    def allowed_imports_path(self) -> Path:
        return Path(self.allowed_imports_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
