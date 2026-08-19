"""Framework-neutral dialogue and incremental presentation values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID, uuid4

from eeveetuber.avatar import CueIntent


class ModelStopReason(StrEnum):
    COMPLETE = "complete"
    LENGTH = "length"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DialogueRequest:
    session_id: UUID
    turn_id: UUID
    generation: int
    user_text: str
    system_context: str
    metadata: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.generation < 0:
            raise ValueError("generation cannot be negative")
        if not self.user_text.strip():
            raise ValueError("user_text cannot be blank")


@dataclass(frozen=True, slots=True)
class ModelTextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ModelCompleted:
    """Normalized model termination and provider-reported token usage."""

    stop_reason: ModelStopReason = ModelStopReason.COMPLETE
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
        ):
            if value is not None and (isinstance(value, bool) or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer")


type ModelStreamEvent = ModelTextDelta | ModelCompleted


@dataclass(frozen=True, slots=True)
class UtteranceSegment:
    sequence: int
    speakable_text: str
    display_text: str
    segment_id: UUID = field(default_factory=uuid4)
    affect: str | None = None
    delivery: str | None = None
    cues: tuple[CueIntent, ...] = ()

    def __post_init__(self) -> None:
        if self.sequence < 0:
            raise ValueError("segment sequence cannot be negative")
        if not self.speakable_text.strip():
            raise ValueError("speakable_text cannot be blank")
        if not self.display_text.strip():
            raise ValueError("display_text cannot be blank")


@dataclass(frozen=True, slots=True)
class UtterancePlan:
    turn_id: UUID
    generation: int
    segments: tuple[UtteranceSegment, ...]
    stop_reason: ModelStopReason
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
        ):
            if value is not None and (isinstance(value, bool) or value < 0):
                raise ValueError(f"{field_name} must be a non-negative integer")

    @property
    def speakable_text(self) -> str:
        return " ".join(segment.speakable_text for segment in self.segments)

    @property
    def display_text(self) -> str:
        return " ".join(segment.display_text for segment in self.segments)


@dataclass(frozen=True, slots=True)
class AudioChunk:
    segment_id: UUID
    sequence: int
    chunk_index: int
    audio: bytes
    media_type: str
    sample_rate_hz: int
    is_final: bool
    duration_ms: int | None = None

    def __post_init__(self) -> None:
        if self.sequence < 0 or self.chunk_index < 0:
            raise ValueError("audio sequence and chunk index cannot be negative")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample rate must be positive")


@dataclass(frozen=True, slots=True)
class SegmentReady:
    turn_id: UUID
    generation: int
    segment: UtteranceSegment


@dataclass(frozen=True, slots=True)
class SegmentAudioReady:
    turn_id: UUID
    generation: int
    segment: UtteranceSegment
    chunk: AudioChunk


@dataclass(frozen=True, slots=True)
class UtteranceCompleted:
    turn_id: UUID
    generation: int
    plan: UtterancePlan


type DialogueStreamEvent = SegmentReady | SegmentAudioReady | UtteranceCompleted
