"""HTTP adapters for the OpenAI-compatible API surface."""

from eeveetuber.adapters.openai_compatible.model import (
    ModelHTTPError,
    ModelProtocolError,
    ModelTransportError,
    OpenAICompatibleModelConfig,
    OpenAICompatibleModelError,
    OpenAICompatibleModelProvider,
    ReasoningEffort,
)
from eeveetuber.adapters.openai_compatible.speech import (
    OpenAICompatibleSpeechConfig,
    OpenAICompatibleSpeechSynthesizer,
    SpeechAdapterClosed,
    SpeechAdapterError,
    SpeechAudioFormat,
    SpeechHTTPError,
    SpeechProtocolError,
    SpeechTimeoutError,
    SpeechTransportError,
)

__all__ = [
    "ModelHTTPError",
    "ModelProtocolError",
    "ModelTransportError",
    "OpenAICompatibleModelConfig",
    "OpenAICompatibleModelError",
    "OpenAICompatibleModelProvider",
    "OpenAICompatibleSpeechConfig",
    "OpenAICompatibleSpeechSynthesizer",
    "ReasoningEffort",
    "SpeechAdapterClosed",
    "SpeechAdapterError",
    "SpeechAudioFormat",
    "SpeechHTTPError",
    "SpeechProtocolError",
    "SpeechTimeoutError",
    "SpeechTransportError",
]
