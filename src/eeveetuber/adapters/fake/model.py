"""Deterministic streaming model adapter with no network dependency."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

from eeveetuber.dialogue.types import (
    DialogueRequest,
    ModelCompleted,
    ModelStreamEvent,
    ModelTextDelta,
)


class FakeModelProvider:
    def __init__(
        self,
        response: str | Callable[[DialogueRequest], str] = "Hello! I am Eeveetuber.",
        *,
        chunk_chars: int = 8,
        delay_seconds: float = 0.0,
    ) -> None:
        if chunk_chars <= 0:
            raise ValueError("chunk_chars must be positive")
        self._response = response
        self._chunk_chars = chunk_chars
        self._delay_seconds = delay_seconds
        self.requests: list[DialogueRequest] = []

    async def stream(self, request: DialogueRequest) -> AsyncIterator[ModelStreamEvent]:
        self.requests.append(request)
        response = self._response(request) if callable(self._response) else self._response
        for offset in range(0, len(response), self._chunk_chars):
            if self._delay_seconds:
                await asyncio.sleep(self._delay_seconds)
            yield ModelTextDelta(response[offset : offset + self._chunk_chars])
        yield ModelCompleted(output_tokens=max(1, len(response) // 4))

