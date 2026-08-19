"""Provider ports kept free of SDK-specific request and response types."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from eeveetuber.dialogue.types import (
    AudioChunk,
    DialogueRequest,
    ModelStreamEvent,
    UtteranceSegment,
)


class ModelProvider(Protocol):
    """Stream normalized model events for one immutable dialogue request."""

    def stream(self, request: DialogueRequest) -> AsyncIterator[ModelStreamEvent]: ...


class SpeechSynthesizer(Protocol):
    """Stream cancellable audio for one validated utterance segment."""

    def synthesize(self, segment: UtteranceSegment) -> AsyncIterator[AudioChunk]: ...
