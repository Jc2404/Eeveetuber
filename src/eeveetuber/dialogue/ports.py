"""Provider ports kept free of SDK-specific request and response types."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from eeveetuber.dialogue.types import (
    AudioChunk,
    DialogueRequest,
    ModelStreamEvent,
    UtteranceSegment,
)
from eeveetuber.runtime.cancellation import CancellationToken


class ModelProvider(Protocol):
    """Stream normalized model events for one immutable dialogue request."""

    def stream(
        self,
        request: DialogueRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[ModelStreamEvent]: ...


class SpeechSynthesizer(Protocol):
    """Stream cancellable audio for one validated utterance segment."""

    def synthesize(
        self,
        segment: UtteranceSegment,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AudioChunk]: ...


@runtime_checkable
class AsyncCloseable(Protocol):
    """Optional lifecycle contract for adapters that own network clients."""

    async def aclose(self) -> None: ...
