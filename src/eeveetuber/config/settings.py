"""Versioned, environment-backed settings for the composition root."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from platformdirs import user_data_path
from pydantic import BaseModel, Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from eeveetuber.config.providers import (
    AsrAdapterSettings,
    ModelAdapterSettings,
    SpeechAdapterSettings,
)


class ContextBudgetSettings(BaseModel):
    """Hard heuristic-token budgets used by the P0 context compiler."""

    t0_canon_tokens: int = Field(default=512, ge=32, le=32_000)
    t1_hot_tokens: int = Field(default=768, ge=0, le=64_000)
    t2_map_tokens: int = Field(default=384, ge=0, le=32_000)
    total_tokens: int = Field(default=1_664, ge=128, le=128_000)
    local_recall_timeout_ms: int = Field(default=75, ge=0, le=1_000)


class ConversationHistorySettings(BaseModel):
    """Bounds for recent transcript context loaded onto the realtime path."""

    max_messages: int = Field(default=12, ge=0, le=1_000)
    max_chars: int = Field(default=6_000, ge=0)
    max_message_chars: int = Field(default=1_500, gt=0)
    load_timeout_ms: int = Field(default=50, ge=0)


class VoiceInputSettings(BaseModel):
    """Validated realtime voice-capture, VAD, and ASR coordination bounds."""

    enabled: bool = True
    sample_rate_hz: int = Field(default=16_000, ge=8_000, le=48_000)
    channels: int = Field(default=1, ge=1, le=1)
    frame_duration_ms: int = Field(default=20, ge=10, le=100)
    max_frame_bytes: int = Field(default=8_192, ge=2, le=1024 * 1024)
    speech_start_threshold: int = Field(default=1_200, ge=1, le=32_767)
    speech_end_threshold: int = Field(default=700, ge=1, le=32_767)
    speech_start_frames: int = Field(default=2, ge=1, le=100)
    speech_end_frames: int = Field(default=5, ge=1, le=500)
    pre_roll_frames: int = Field(default=5, ge=0, le=500)
    max_utterance_duration_ms: int = Field(default=30_000, ge=100, le=120_000)
    max_utterance_bytes: int = Field(default=1024 * 1024, ge=2, le=512 * 1024 * 1024)
    asr_timeout_ms: int = Field(default=30_000, ge=100, le=600_000)
    max_pending_utterances: int = Field(default=2, ge=1, le=8)
    max_transcript_chars: int = Field(default=32_000, ge=1, le=32_000)
    barge_in_enabled: bool = True

    @model_validator(mode="after")
    def validate_cross_field_bounds(self) -> VoiceInputSettings:
        sample_numerator = self.sample_rate_hz * self.frame_duration_ms
        if sample_numerator % 1_000:
            raise ValueError(
                "one complete 16-bit PCM frame requires an integral sample count"
            )
        minimum_frame_bytes = (
            sample_numerator // 1_000
        ) * self.channels * 2
        if self.max_frame_bytes < minimum_frame_bytes:
            raise ValueError(
                "max_frame_bytes must hold one complete 16-bit PCM frame "
                f"({minimum_frame_bytes} bytes for the configured format)"
            )
        if self.max_utterance_bytes < self.max_frame_bytes:
            raise ValueError("max_utterance_bytes must be at least max_frame_bytes")
        required_start_bytes = minimum_frame_bytes * self.speech_start_frames
        if self.max_utterance_bytes < required_start_bytes:
            raise ValueError(
                "max_utterance_bytes must hold the configured speech_start_frames "
                f"({required_start_bytes} bytes)"
            )
        required_start_duration_ms = self.frame_duration_ms * self.speech_start_frames
        if self.max_utterance_duration_ms < required_start_duration_ms:
            raise ValueError(
                "max_utterance_duration_ms must span the configured speech_start_frames "
                f"({required_start_duration_ms} ms)"
            )
        if self.speech_end_threshold > self.speech_start_threshold:
            raise ValueError("speech_end_threshold must not exceed speech_start_threshold")
        return self


class AvatarRendererSetting(StrEnum):
    """Renderer implementations selectable at the composition boundary."""

    LIVE2D_WEB = "live2d_web"


class AvatarSettings(BaseModel):
    """Optional owner-supplied avatar assets and bounded command transport."""

    enabled: bool = False
    renderer: AvatarRendererSetting = AvatarRendererSetting.LIVE2D_WEB
    asset_dir: Path | None = None
    manifest_filename: str = Field(default="avatar.json", min_length=1, max_length=255)
    command_queue_capacity: int = Field(default=128, ge=8, le=4_096)

    @field_validator("manifest_filename")
    @classmethod
    def validate_manifest_filename(cls, value: str) -> str:
        if (
            not value.strip()
            or value in {".", ".."}
            or Path(value).name != value
            or "\\" in value
        ):
            raise ValueError("manifest_filename must be a plain filename, not a path")
        return value

    @model_validator(mode="after")
    def validate_enabled_assets(self) -> AvatarSettings:
        if self.enabled and self.asset_dir is None:
            raise ValueError("avatar asset_dir is required when the renderer is enabled")
        return self


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
    history: ConversationHistorySettings = Field(default_factory=ConversationHistorySettings)
    voice: VoiceInputSettings = Field(default_factory=VoiceInputSettings)
    avatar: AvatarSettings = Field(default_factory=AvatarSettings)
    model: ModelAdapterSettings = Field(default_factory=ModelAdapterSettings)
    speech: SpeechAdapterSettings = Field(default_factory=SpeechAdapterSettings)
    asr: AsrAdapterSettings = Field(default_factory=AsrAdapterSettings)

    @field_validator("log_filename")
    @classmethod
    def validate_log_filename(cls, value: str) -> str:
        if not value.strip() or value in {".", ".."} or Path(value).name != value:
            raise ValueError("log_filename must be a plain filename, not a path")
        return value

    @model_validator(mode="after")
    def validate_voice_asr_capacity(self) -> AppSettings:
        if self.voice.max_utterance_bytes > self.asr.max_input_pcm_bytes:
            raise ValueError(
                "voice max_utterance_bytes cannot exceed ASR max_input_pcm_bytes"
            )
        return self

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
