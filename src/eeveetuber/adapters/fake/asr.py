"""Deterministic in-process ASR adapter for tests and vertical tracing."""

from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from uuid import UUID

from eeveetuber.media import AsrFinal, AsrPartial, AsrStreamEvent, PcmUtterance
from eeveetuber.runtime.cancellation import CancellationToken


@dataclass(frozen=True, slots=True)
class FakeAsrRequestRecord:
    """Non-audio request metadata retained for deterministic assertions."""

    utterance_id: UUID
    stream_id: UUID
    byte_count: int
    frame_count: int


class FakeSpeechRecognizer:
    """Yield configured hypotheses without retaining or persisting raw PCM."""

    def __init__(
        self,
        transcript: str | Callable[[PcmUtterance], str] = "Hello from fake ASR.",
        *,
        partials: tuple[str, ...] = (),
        language: str | None = "en",
        confidence: float | None = 1.0,
        delay_seconds: float = 0.0,
    ) -> None:
        if not callable(transcript) and not isinstance(transcript, str):
            raise TypeError("transcript must be a string or callable")
        if any(not isinstance(partial, str) for partial in partials):
            raise TypeError("ASR partials must be strings")
        if language is not None and not language.strip():
            raise ValueError("language cannot be blank")
        if confidence is not None and (
            not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0
        ):
            raise ValueError("confidence must be finite and between 0.0 and 1.0")
        if not math.isfinite(delay_seconds) or delay_seconds < 0:
            raise ValueError("delay_seconds must be finite and non-negative")
        self._transcript = transcript
        self._partials = partials
        self._language = language
        self._confidence = confidence
        self._delay_seconds = delay_seconds
        self.requests: list[FakeAsrRequestRecord] = []

    async def recognize(
        self,
        utterance: PcmUtterance,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AsrStreamEvent]:
        self.requests.append(
            FakeAsrRequestRecord(
                utterance_id=utterance.utterance_id,
                stream_id=utterance.stream_id,
                byte_count=utterance.byte_count,
                frame_count=len(utterance.frames),
            )
        )
        for revision, text in enumerate(self._partials):
            await self._wait(cancellation)
            yield AsrPartial(
                utterance_id=utterance.utterance_id,
                revision=revision,
                text=text,
                language=self._language,
                confidence=self._confidence,
            )

        await self._wait(cancellation)
        transcript = (
            self._transcript(utterance) if callable(self._transcript) else self._transcript
        )
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        yield AsrFinal(
            utterance_id=utterance.utterance_id,
            text=transcript,
            language=self._language,
            confidence=self._confidence,
        )

    async def _wait(self, cancellation: CancellationToken | None) -> None:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        if cancellation is not None:
            cancellation.raise_if_cancelled()


__all__ = ["FakeAsrRequestRecord", "FakeSpeechRecognizer"]
