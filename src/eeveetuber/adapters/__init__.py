"""External provider and renderer adapters."""

from eeveetuber.adapters.factory import (
    create_model_provider,
    create_speech_recognizer,
    create_speech_synthesizer,
)

__all__ = [
    "create_model_provider",
    "create_speech_recognizer",
    "create_speech_synthesizer",
]
