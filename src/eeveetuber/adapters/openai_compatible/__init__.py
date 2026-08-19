"""HTTP adapters for the OpenAI-compatible API surface."""

from eeveetuber.adapters.openai_compatible.asr import (
    AsrAdapterClosed,
    AsrAdapterError,
    AsrHTTPError,
    AsrProtocolError,
    AsrTimeoutError,
    AsrTransportError,
    OpenAICompatibleAsrConfig,
    OpenAICompatibleSpeechRecognizer,
)
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
    "AsrAdapterClosed",
    "AsrAdapterError",
    "AsrHTTPError",
    "AsrProtocolError",
    "AsrTimeoutError",
    "AsrTransportError",
    "ModelHTTPError",
    "ModelProtocolError",
    "ModelTransportError",
    "OpenAICompatibleAsrConfig",
    "OpenAICompatibleModelConfig",
    "OpenAICompatibleModelError",
    "OpenAICompatibleModelProvider",
    "OpenAICompatibleSpeechConfig",
    "OpenAICompatibleSpeechRecognizer",
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
