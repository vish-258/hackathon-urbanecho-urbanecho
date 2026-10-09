"""Validated runtime configuration. Database credentials never have defaults."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str | None = None
    db_host: str = "db"
    db_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = "noise_monitor"
    app_db_user: str = "noise_app"
    app_db_password: str | None = None
    admin_token: str = Field(min_length=32)
    # Enable only with the API's host port bound to loopback. This is not a
    # replacement for administrator sign-in on a shared or public deployment.
    local_browser_access: bool = False
    local_browser_port: int = Field(default=8000, ge=1, le=65535)
    audio_root: Path = Path("/data/audio")
    max_upload_bytes: int = Field(default=20_000_000, gt=44)
    max_duration_seconds: float = Field(default=60, gt=0, le=600)
    allowed_sample_rates: list[int] = [16000, 32000, 44100, 48000]
    db_retry_attempts: int = Field(default=5, ge=1, le=30)
    db_retry_delay_seconds: float = Field(default=1, ge=0, le=30)
    worker_poll_seconds: float = Field(default=1, gt=0)
    job_lease_seconds: int = Field(default=120, ge=10)
    job_max_attempts: int = Field(default=5, ge=1)
    job_retry_base_seconds: float = Field(default=2, gt=0)
    daily_schedule_enabled: bool = True
    daily_schedule_minute: int = Field(default=5, ge=0, le=1439)
    daily_schedule_poll_seconds: float = Field(default=60, ge=1, le=3600)
    daily_job_lease_seconds: int = Field(default=600, ge=10, le=3600)
    daily_job_max_attempts: int = Field(default=5, ge=1, le=20)
    # Runs in a separate process/image; sound-level workers never load ML dependencies.
    classification_enabled: bool = True
    classification_scope: Literal["incidents", "recordings"] = "incidents"
    classification_poll_seconds: float = Field(default=1, ge=0.1, le=60)
    classification_scan_batch_size: int = Field(default=32, ge=2, le=200)
    classification_lease_seconds: int = Field(default=120, ge=10, le=3600)
    classification_max_attempts: int = Field(default=3, ge=1, le=10)
    classification_retry_base_seconds: float = Field(default=10, ge=0.1, le=3600)
    classification_model_path: Path = Path('/opt/urbanecho-models/yamnet.tflite')
    incident_context_before_seconds: int = Field(default=5, ge=0, le=60)
    incident_context_after_seconds: int = Field(default=5, ge=0, le=60)
    incident_analysis_refresh_seconds: float = Field(default=10, ge=1, le=300)
    incident_analysis_settle_seconds: float = Field(default=10, ge=0, le=300)
    live_freshness_seconds: float = Field(default=120, gt=0, le=86400)
    future_skew_seconds: float = Field(default=5, ge=0, le=300)
    data_stale_seconds: float = Field(default=30, gt=0, le=86400)
    recovery_count: int = Field(default=3, ge=1, le=100)
    event_replay_seconds: int = Field(default=604800, ge=1)
    sse_poll_seconds: float = Field(default=0.25, ge=0.01, le=30)
    sse_heartbeat_seconds: float = Field(default=10, ge=0.05, le=60)
    sse_batch_size: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def validate_database(self):
        if not self.database_url:
            if not self.app_db_password:
                raise ValueError("Set DATABASE_URL or APP_DB_PASSWORD; credentials have no defaults")
            self.database_url = URL.create(
                "postgresql+psycopg", username=self.app_db_user,
                password=self.app_db_password, host=self.db_host,
                port=self.db_port, database=self.postgres_db,
            ).render_as_string(hide_password=False)
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use the postgresql+psycopg driver")
        if not self.allowed_sample_rates or any(rate <= 0 for rate in self.allowed_sample_rates):
            raise ValueError("ALLOWED_SAMPLE_RATES must contain positive sample rates")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
