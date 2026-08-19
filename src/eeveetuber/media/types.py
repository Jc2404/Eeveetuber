"""Framework-neutral, in-memory audio and speech-recognition values.

Raw PCM belongs to the live capture pipeline only.  These immutable values define
bounded hand-off contracts; they intentionally provide no serialization or
persistence behavior.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class PcmEncoding(StrEnum):
    """PCM encodings accepted by the voice-input core."""

    S16_LE = "pcm_s16le"


@dataclass(frozen=True, slots=True)
class PcmFormat:
    """The format shared by every frame in one capture stream."""

    sample_rate_hz: int
    channels: int = 1
    encoding: PcmEncoding = PcmEncoding.S16_LE

    def __post_init__(self) -> None:
        if (
            isinstance(self.sample_rate_hz, bool)
            or not isinstance(self.sample_rate_hz, int)
            or not 8_000 <= self.sample_rate_hz <= 192_000
        ):
            raise ValueError("sample_rate_hz must be between 8000 and 192000")
        if (
            isinstance(self.channels, bool)
            or not isinstance(self.channels, int)
            or not 1 <= self.channels <= 8
        ):
            raise ValueError("channels must be between 1 and 8")
        if self.encoding is not PcmEncoding.S16_LE:
            raise ValueError("only signed 16-bit little-endian PCM is supported")

    @property
    def bytes_per_sample_frame(self) -> int:
        return self.channels * 2


@dataclass(frozen=True, slots=True)
class PcmFrame:
    """One timestamped frame from a uniquely identified live PCM stream."""

    stream_id: UUID
    sequence: int
    captured_at_monotonic_ns: int
    format: PcmFormat
    pcm: bytes

    def __post_init__(self) -> None:
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 0
        ):
            raise ValueError("frame sequence must be a non-negative integer")
        if (
            isinstance(self.captured_at_monotonic_ns, bool)
            or not isinstance(self.captured_at_monotonic_ns, int)
            or self.captured_at_monotonic_ns < 0
        ):
            raise ValueError("captured_at_monotonic_ns must be a non-negative integer")
        if not isinstance(self.pcm, bytes):
            raise TypeError("pcm must be immutable bytes")
        if not self.pcm:
            raise ValueError("pcm frame cannot be empty")
        if len(self.pcm) % self.format.bytes_per_sample_frame:
            raise ValueError("pcm length must contain complete sample frames")

    @property
    def sample_frame_count(self) -> int:
        return len(self.pcm) // self.format.bytes_per_sample_frame

    @property
    def duration_ns(self) -> int:
        return self.sample_frame_count * 1_000_000_000 // self.format.sample_rate_hz

    @property
    def end_monotonic_ns(self) -> int:
        return self.captured_at_monotonic_ns + self.duration_ns


class UtteranceEndReason(StrEnum):
    SILENCE = "silence"
    MAX_DURATION = "max_duration"
    MAX_BYTES = "max_bytes"
    STREAM_ENDED = "stream_ended"


@dataclass(frozen=True, slots=True)
class PcmUtterance:
    """A finite, VAD-delimited collection of PCM frames held only in memory."""

    utterance_id: UUID
    stream_id: UUID
    format: PcmFormat
    frames: tuple[PcmFrame, ...]
    speech_started_at_monotonic_ns: int
    speech_ended_at_monotonic_ns: int
    end_reason: UtteranceEndReason

    def __post_init__(self) -> None:
        if not isinstance(self.frames, tuple) or not self.frames:
            raise ValueError("utterance must contain at least one PCM frame")
        previous: PcmFrame | None = None
        for frame in self.frames:
            if frame.stream_id != self.stream_id:
                raise ValueError("utterance frames must share the stream_id")
            if frame.format != self.format:
                raise ValueError("utterance frames must share one PCM format")
            if previous is not None:
                if frame.sequence <= previous.sequence:
                    raise ValueError("utterance frame sequences must be strictly increasing")
                if frame.captured_at_monotonic_ns < previous.end_monotonic_ns:
                    raise ValueError("utterance PCM frame timestamps cannot overlap")
            previous = frame

        first_at = self.frames[0].captured_at_monotonic_ns
        captured_end = self.frames[-1].end_monotonic_ns
        if not first_at <= self.speech_started_at_monotonic_ns <= captured_end:
            raise ValueError("speech start must fall within the captured utterance")
        if not self.speech_started_at_monotonic_ns <= self.speech_ended_at_monotonic_ns:
            raise ValueError("speech end cannot precede speech start")
        if self.speech_ended_at_monotonic_ns > captured_end:
            raise ValueError("speech end must fall within the captured utterance")

    @property
    def byte_count(self) -> int:
        return sum(len(frame.pcm) for frame in self.frames)

    @property
    def audio_duration_ns(self) -> int:
        return sum(frame.duration_ns for frame in self.frames)

    @property
    def pcm(self) -> bytes:
        """Return one bounded transient copy for adapters that require a single buffer."""

        return b"".join(frame.pcm for frame in self.frames)


def _validate_transcript_fields(text: str, language: str | None, confidence: float | None) -> None:
    if not isinstance(text, str):
        raise TypeError("transcript text must be a string")
    if language is not None and not language.strip():
        raise ValueError("language cannot be blank")
    if confidence is not None and (
        not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0
    ):
        raise ValueError("confidence must be finite and between 0.0 and 1.0")


@dataclass(frozen=True, slots=True)
class AsrPartial:
    """A replaceable, non-terminal recognition hypothesis."""

    utterance_id: UUID
    revision: int
    text: str
    language: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.revision, bool)
            or not isinstance(self.revision, int)
            or self.revision < 0
        ):
            raise ValueError("ASR partial revision must be a non-negative integer")
        _validate_transcript_fields(self.text, self.language, self.confidence)


@dataclass(frozen=True, slots=True)
class AsrFinal:
    """The required terminal recognition result, which may be empty."""

    utterance_id: UUID
    text: str
    language: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        _validate_transcript_fields(self.text, self.language, self.confidence)


type AsrStreamEvent = AsrPartial | AsrFinal


__all__ = [
    "AsrFinal",
    "AsrPartial",
    "AsrStreamEvent",
    "PcmEncoding",
    "PcmFormat",
    "PcmFrame",
    "PcmUtterance",
    "UtteranceEndReason",
]
