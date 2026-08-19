"""Versioned, environment-backed settings for the composition root."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_path
from pydantic import BaseModel, Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from eeveetuber.config.providers import ModelAdapterSettings, SpeechAdapterSettings


class ContextBudgetSettings(BaseModel):
    """Hard heuristic-token budgets used by the P0 context compiler."""

    t0_canon_tokens: int = Field(default=512, ge=32, le=32_000)
    t1_hot_tokens: int = Field(default=768, ge=0, le=64_000)
    t2_map_tokens: int = Field(default=384, ge=0, le=32_000)
    total_tokens: int = Field(default=1_664, ge=128, le=128_000)
    local_recall_timeout_ms: int = Field(default=75, ge=0, le=1_000)


class AppSettings(BaseSettings):
    """Process settings loaded once by the application composition root."""

    model_config = SettingsConfigDict(
        env_prefix="EEVEETUBER_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="forbid",
    )

    schema_version: int = Field(default=1, frozen=True)
    host: str = "127.0.0.1"
    port: int = Field(default=12_393, ge=1, le=65_535)
    log_level: str = "INFO"
    data_dir: Path = Field(
        default_factory=lambda: user_data_path("Eeveetuber", appauthor=False, ensure_exists=False)
    )
    log_dir: Path | None = None
    log_filename: str = Field(default="eeveetuber.log", min_length=1, max_length=255)
    log_max_bytes: int = Field(default=10 * 1024 * 1024, ge=64 * 1024, le=1024 * 1024 * 1024)
    log_backup_count: int = Field(default=3, ge=1, le=100)
    database_filename: str = "eeveetuber.db"
    session_mailbox_capacity: int = Field(default=128, ge=8, le=65_536)
    websocket_send_capacity: int = Field(default=256, ge=8, le=65_536)
    event_recorder_capacity: int = Field(default=8_192, ge=128, le=1_000_000)
    context: ContextBudgetSettings = Field(default_factory=ContextBudgetSettings)
    model: ModelAdapterSettings = Field(default_factory=ModelAdapterSettings)
    speech: SpeechAdapterSettings = Field(default_factory=SpeechAdapterSettings)

    @field_validator("log_filename")
    @classmethod
    def validate_log_filename(cls, value: str) -> str:
        if not value.strip() or value in {".", ".."} or Path(value).name != value:
            raise ValueError("log_filename must be a plain filename, not a path")
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_filename

    @computed_field  # type: ignore[prop-decorator]
    @property
    def verbose_log_path(self) -> Path:
        """Resolved opt-in text log; no directory is created until requested."""

        directory = self.log_dir if self.log_dir is not None else self.data_dir / "logs"
        return directory / self.log_filename

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return the validated process settings singleton."""

    return AppSettings()
