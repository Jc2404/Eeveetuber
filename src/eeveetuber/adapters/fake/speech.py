"""Deterministic non-audio synthesizer used to exercise streaming contracts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from eeveetuber.dialogue.types import AudioChunk, UtteranceSegment


class FakeSpeechSynthesizer:
    media_type = "audio/x-eeveetuber-fake"

    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self._delay_seconds = delay_seconds
        self.segments: list[UtteranceSegment] = []

    async def synthesize(self, segment: UtteranceSegment) -> AsyncIterator[AudioChunk]:
        self.segments.append(segment)
        if self._delay_seconds:
            await asyncio.sleep(self._delay_seconds)
        payload = f"FAKE_AUDIO:{segment.speakable_text}".encode()
        yield AudioChunk(
            segment_id=segment.segment_id,
            sequence=segment.sequence,
            chunk_index=0,
            audio=payload,
            media_type=self.media_type,
            sample_rate_hz=24_000,
            is_final=True,
            duration_ms=max(80, len(segment.speakable_text) * 35),
        )

