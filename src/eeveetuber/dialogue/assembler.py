"""Incrementally turn arbitrary model text deltas into validated speech segments."""

from __future__ import annotations

import re

from eeveetuber.dialogue.types import UtteranceSegment

_BOUNDARY = re.compile(r"[.!?](?:\s+|$)|[\u3002\uff01\uff1f]|\n+")


class IncrementalUtteranceAssembler:
    """Emit complete sentences early and cap latency for punctuation-free output."""

    def __init__(self, *, max_segment_chars: int = 220) -> None:
        if max_segment_chars < 20:
            raise ValueError("max_segment_chars must be at least 20")
        self._max_segment_chars = max_segment_chars
        self._buffer = ""
        self._segments: list[UtteranceSegment] = []

    @property
    def segments(self) -> tuple[UtteranceSegment, ...]:
        return tuple(self._segments)

    def push(self, text_delta: str) -> tuple[UtteranceSegment, ...]:
        if not text_delta:
            return ()
        self._buffer += text_delta
        emitted: list[UtteranceSegment] = []

        while True:
            boundary = _BOUNDARY.search(self._buffer)
            if boundary is not None:
                end = boundary.start() if self._buffer[boundary.start()] == "\n" else boundary.start() + 1
                candidate = self._buffer[:end].strip()
                self._buffer = self._buffer[boundary.end() :]
                if candidate:
                    emitted.append(self._make_segment(candidate))
                continue

            if len(self._buffer) >= self._max_segment_chars:
                cut = self._safe_cut(self._buffer, self._max_segment_chars)
                candidate = self._buffer[:cut].strip()
                self._buffer = self._buffer[cut:].lstrip()
                if candidate:
                    emitted.append(self._make_segment(candidate))
                continue
            break

        return tuple(emitted)

    def finish(self) -> tuple[UtteranceSegment, ...]:
        candidate = self._buffer.strip()
        self._buffer = ""
        if not candidate:
            return ()
        return (self._make_segment(candidate),)

    def _make_segment(self, text: str) -> UtteranceSegment:
        segment = UtteranceSegment(
            sequence=len(self._segments),
            speakable_text=text,
            display_text=text,
        )
        self._segments.append(segment)
        return segment

    @staticmethod
    def _safe_cut(text: str, preferred: int) -> int:
        whitespace = text.rfind(" ", 0, preferred + 1)
        return whitespace if whitespace >= preferred // 2 else preferred
