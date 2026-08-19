"""Composition helpers that turn validated settings into provider-neutral adapters."""

from __future__ import annotations

from eeveetuber.adapters.fake import FakeModelProvider, FakeSpeechSynthesizer
from eeveetuber.adapters.openai_compatible import (
    OpenAICompatibleModelConfig,
    OpenAICompatibleModelProvider,
    OpenAICompatibleSpeechConfig,
    OpenAICompatibleSpeechSynthesizer,
    ReasoningEffort,
    SpeechAudioFormat,
)
from eeveetuber.config import AdapterProvider, ModelAdapterSettings, SpeechAdapterSettings
from eeveetuber.dialogue.ports import ModelProvider, SpeechSynthesizer


def create_model_provider(settings: ModelAdapterSettings) -> ModelProvider:
    """Create one session-owned model adapter without performing network I/O."""

    if settings.provider is AdapterProvider.FAKE:
        return FakeModelProvider(lambda request: f"I heard you say: {request.user_text}.")
    if settings.provider is AdapterProvider.OPENAI_COMPATIBLE:
        reasoning_effort = (
            ReasoningEffort(settings.reasoning_effort.value)
            if settings.reasoning_effort is not None
            else None
        )
        return OpenAICompatibleModelProvider(
            OpenAICompatibleModelConfig(
                base_url=settings.base_url,
                api_key=_secret_value(settings.api_key),
                model=settings.model,
                timeout_seconds=settings.request_timeout_seconds,
                connect_timeout_seconds=settings.connect_timeout_seconds,
                max_tokens=settings.max_output_tokens,
                temperature=settings.temperature,
                reasoning_effort=reasoning_effort,
            )
        )
    raise ValueError(f"unsupported model provider {settings.provider!r}")


def create_speech_synthesizer(settings: SpeechAdapterSettings) -> SpeechSynthesizer:
    """Create one session-owned speech adapter without performing network I/O."""

    if settings.provider is AdapterProvider.FAKE:
        return FakeSpeechSynthesizer()
    if settings.provider is AdapterProvider.OPENAI_COMPATIBLE:
        return OpenAICompatibleSpeechSynthesizer(
            OpenAICompatibleSpeechConfig(
                base_url=settings.base_url,
                api_key=_secret_value(settings.api_key),
                model=settings.model,
                voice=settings.voice,
                response_format=SpeechAudioFormat(settings.response_format.value),
                sample_rate_hz=settings.sample_rate_hz,
                timeout_seconds=settings.request_timeout_seconds,
                connect_timeout_seconds=settings.connect_timeout_seconds,
                chunk_size_bytes=settings.stream_chunk_bytes,
                max_response_bytes=settings.max_response_bytes,
                send_sample_rate=settings.send_sample_rate,
                use_segment_instructions=settings.use_segment_instructions,
                pcm_channels=settings.pcm_channels,
                pcm_sample_width_bytes=settings.pcm_sample_width_bytes,
            )
        )
    raise ValueError(f"unsupported speech provider {settings.provider!r}")


def _secret_value(secret: object) -> str | None:
    if secret is None:
        return None
    getter = getattr(secret, "get_secret_value", None)
    if not callable(getter):  # pragma: no cover - Pydantic validates the field type
        raise TypeError("provider secret has an invalid type")
    value = getter()
    if not isinstance(value, str):  # pragma: no cover - defensive provider boundary
        raise TypeError("provider secret must resolve to text")
    return value


__all__ = ["create_model_provider", "create_speech_synthesizer"]
