"""Validated provider configuration kept separate from provider SDK types."""

from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, SecretStr, field_validator


class AdapterProvider(StrEnum):
    """Runtime-selectable adapter families supported by the composition root."""

    FAKE = "fake"
    OPENAI_COMPATIBLE = "openai_compatible"


class ReasoningEffortSetting(StrEnum):
    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SpeechOutputFormat(StrEnum):
    MP3 = "mp3"
    OPUS = "opus"
    AAC = "aac"
    FLAC = "flac"
    WAV = "wav"
    PCM = "pcm"


class AsrAdapterSettings(BaseModel):
    """Configuration for fake or OpenAI-compatible speech recognition."""

    provider: AdapterProvider = AdapterProvider.FAKE
    base_url: str = "https://api.openai.com/v1"
    api_key: SecretStr | None = None
    model: str | None = "whisper-1"
    language: str | None = None
    prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=1)
    request_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    max_input_pcm_bytes: int = Field(
        default=64 * 1_024 * 1_024,
        ge=2,
        le=512 * 1_024 * 1_024,
    )
    max_response_bytes: int = Field(
        default=1 * 1_024 * 1_024,
        ge=64,
        le=64 * 1_024 * 1_024,
    )

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        return normalized

    @field_validator("api_key")
    @classmethod
    def nonblank_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            raw_value = value.get_secret_value()
            if not raw_value or raw_value.strip() != raw_value:
                raise ValueError(
                    "api_key must be non-empty and trimmed; use None for local endpoints"
                )
        return value

    @field_validator("model", "language", "prompt")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("optional ASR text settings cannot be blank")
        return normalized


class ModelAdapterSettings(BaseModel):
    """Configuration shared by hosted and local OpenAI-compatible model endpoints."""

    provider: AdapterProvider = AdapterProvider.FAKE
    base_url: str = "https://api.openai.com/v1"
    api_key: SecretStr | None = None
    model: str = "gpt-4.1-mini"
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    max_output_tokens: int | None = Field(default=1_024, ge=1, le=1_000_000)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: ReasoningEffortSetting | None = None

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("base_url must use http or https")
        return normalized

    @field_validator("model")
    @classmethod
    def nonblank_model(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model cannot be blank")
        return normalized


class SpeechAdapterSettings(BaseModel):
    """Configuration for streaming speech synthesis endpoints."""

    provider: AdapterProvider = AdapterProvider.FAKE
    base_url: str = "https://api.openai.com/v1"
    api_key: SecretStr | None = None
    model: str = "gpt-4o-mini-tts"
    voice: str = "alloy"
    response_format: SpeechOutputFormat = SpeechOutputFormat.MP3
    sample_rate_hz: int = Field(default=24_000, ge=8_000, le=192_000)
    stream_chunk_bytes: int = Field(default=16_384, ge=1_024, le=1_048_576)
    max_response_bytes: int = Field(default=64 * 1_024 * 1_024, ge=1_024, le=512 * 1_024 * 1_024)
    request_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    connect_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    send_sample_rate: bool = False
    use_segment_instructions: bool = False
    pcm_channels: int = Field(default=1, ge=1, le=8)
    pcm_sample_width_bytes: int = Field(default=2, ge=1, le=4)

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("base_url must use http or https")
        return normalized

    @field_validator("model", "voice")
    @classmethod
    def nonblank_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider string values cannot be blank")
        return normalized


__all__ = [
    "AdapterProvider",
    "AsrAdapterSettings",
    "ModelAdapterSettings",
    "ReasoningEffortSetting",
    "SpeechAdapterSettings",
    "SpeechOutputFormat",
]
