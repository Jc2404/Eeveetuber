"""Typed contracts shared by the performance director and scheduler."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Final

from .intents import PresentationLayer, SemanticIntent


def _require_identifier(value: str, field_name: str) -> None:
    if not value or value.strip() != value:
        raise ValueError(f"{field_name} must be a non-empty, trimmed string")


def _require_finite(value: float, field_name: str) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be finite")


def _require_non_negative(value: float, field_name: str) -> None:
    _require_finite(value, field_name)
    if value < 0.0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_positive(value: float, field_name: str) -> None:
    _require_finite(value, field_name)
    if value <= 0.0:
        raise ValueError(f"{field_name} must be greater than zero")


class CueSource(StrEnum):
    """Host-assigned origin of a cue; the model cannot grant itself authority."""

    OPERATOR = "operator"
    SPEECH_SYNC = "speech_sync"
    SCRIPTED_SHOW = "scripted_show"
    INTERACTION_STATE = "interaction_state"
    MODEL = "model"
    GAME_EVENT = "game_event"
    REACTIVE_IDLE = "reactive_idle"


class CuePriority(IntEnum):
    """Plan section 11.2 priority order (larger values win arbitration)."""

    REACTIVE_IDLE = 10
    GAME_EVENT = 20
    MODEL = 30
    INTERACTION_STATE = 40
    SCRIPTED_SHOW = 50
    SPEECH_SYNC = 60
    OPERATOR_OVERRIDE = 70


_SOURCE_PRIORITIES: Final[dict[CueSource, CuePriority]] = {
    CueSource.REACTIVE_IDLE: CuePriority.REACTIVE_IDLE,
    CueSource.GAME_EVENT: CuePriority.GAME_EVENT,
    CueSource.MODEL: CuePriority.MODEL,
    CueSource.INTERACTION_STATE: CuePriority.INTERACTION_STATE,
    CueSource.SCRIPTED_SHOW: CuePriority.SCRIPTED_SHOW,
    CueSource.SPEECH_SYNC: CuePriority.SPEECH_SYNC,
    CueSource.OPERATOR: CuePriority.OPERATOR_OVERRIDE,
}


def priority_for_source(source: CueSource) -> CuePriority:
    return _SOURCE_PRIORITIES[source]


class BlendMode(StrEnum):
    REPLACE = "replace"
    ADDITIVE = "additive"
    CROSS_FADE = "cross_fade"


class BlendCurve(StrEnum):
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"


@dataclass(frozen=True, slots=True)
class BlendSpec:
    """Renderer-facing blend metadata; the scheduler does not render frames."""

    mode: BlendMode = BlendMode.CROSS_FADE
    curve: BlendCurve = BlendCurve.EASE_IN_OUT
    fade_in_s: float = 0.12
    fade_out_s: float = 0.16
    weight: float = 1.0

    def __post_init__(self) -> None:
        _require_non_negative(self.fade_in_s, "fade_in_s")
        _require_non_negative(self.fade_out_s, "fade_out_s")
        if not math.isfinite(self.weight) or not 0.0 <= self.weight <= 1.0:
            raise ValueError("weight must be finite and between 0.0 and 1.0")


class AudioAnchor(StrEnum):
    UTTERANCE_START = "utterance_start"
    SEGMENT_START = "segment_start"
    WORD = "word"
    VISEME = "viseme"
    UTTERANCE_END = "utterance_end"


_MARKER_ANCHORS: Final[frozenset[AudioAnchor]] = frozenset(
    {AudioAnchor.SEGMENT_START, AudioAnchor.WORD, AudioAnchor.VISEME}
)


@dataclass(frozen=True, slots=True)
class AudioTimelineBinding:
    """Bind a cue to a host-provided audio marker.

    The offset is scheduler-clock metadata supplied by the speech pipeline, not
    exact timing requested by a language model.
    """

    utterance_id: str
    anchor: AudioAnchor = AudioAnchor.UTTERANCE_START
    marker_id: str | None = None
    offset_s: float = 0.0

    def __post_init__(self) -> None:
        _require_identifier(self.utterance_id, "utterance_id")
        _require_finite(self.offset_s, "offset_s")
        if self.anchor in _MARKER_ANCHORS and self.marker_id is None:
            raise ValueError(f"marker_id is required for {self.anchor.value}")
        if self.marker_id is not None:
            _require_identifier(self.marker_id, "marker_id")


@dataclass(frozen=True, slots=True)
class AudioTimelineMarker:
    """A point on the playback timeline in the scheduler clock domain."""

    utterance_id: str
    generation: int
    anchor: AudioAnchor
    occurred_at: float
    marker_id: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.utterance_id, "utterance_id")
        if self.generation < 0:
            raise ValueError("generation must be non-negative")
        _require_finite(self.occurred_at, "occurred_at")
        if self.anchor in _MARKER_ANCHORS and self.marker_id is None:
            raise ValueError(f"marker_id is required for {self.anchor.value}")
        if self.marker_id is not None:
            _require_identifier(self.marker_id, "marker_id")

    @property
    def key(self) -> tuple[str, AudioAnchor, str | None]:
        return (self.utterance_id, self.anchor, self.marker_id)


@dataclass(frozen=True, slots=True)
class CueRequest:
    """Host-owned scheduling envelope around a cognition-safe semantic intent."""

    cue_id: str
    generation: int
    intent: SemanticIntent
    requested_at: float
    source: CueSource = CueSource.MODEL
    priority: CuePriority | None = None
    ttl_s: float = 2.0
    lease_s: float | None = None
    audio_binding: AudioTimelineBinding | None = None
    limits_exempt: bool = False

    def __post_init__(self) -> None:
        _require_identifier(self.cue_id, "cue_id")
        if self.generation < 0:
            raise ValueError("generation must be non-negative")
        if not isinstance(self.intent, SemanticIntent):
            raise TypeError("intent must implement SemanticIntent")
        _require_finite(self.requested_at, "requested_at")
        _require_positive(self.ttl_s, "ttl_s")
        if self.lease_s is not None:
            _require_positive(self.lease_s, "lease_s")

    @property
    def effective_priority(self) -> CuePriority:
        return self.priority if self.priority is not None else priority_for_source(self.source)

    @property
    def not_after(self) -> float:
        return self.requested_at + self.ttl_s


@dataclass(frozen=True, slots=True)
class PresentationCue:
    """A fully resolved, avatar-specific cue accepted by the scheduler."""

    cue_id: str
    generation: int
    layer: PresentationLayer
    semantic_key: str
    adapter_action: str | None
    intensity: float
    priority: CuePriority
    requested_at: float
    not_after: float | None
    lease_s: float | None
    resources: frozenset[str] = field(default_factory=frozenset)
    blend: BlendSpec = field(default_factory=BlendSpec)
    cooldown_key: str | None = None
    cooldown_s: float = 0.0
    audio_binding: AudioTimelineBinding | None = None
    source: CueSource = CueSource.MODEL
    neutral: bool = False
    limits_exempt: bool = False
    fallback_from: str | None = None

    def __post_init__(self) -> None:
        _require_identifier(self.cue_id, "cue_id")
        if self.generation < 0:
            raise ValueError("generation must be non-negative")
        _require_identifier(self.semantic_key, "semantic_key")
        if not self.semantic_key.startswith(f"{self.layer.value}."):
            raise ValueError("semantic_key namespace must match layer")
        if self.adapter_action is not None:
            _require_identifier(self.adapter_action, "adapter_action")
        if not math.isfinite(self.intensity) or not 0.0 <= self.intensity <= 1.0:
            raise ValueError("intensity must be finite and between 0.0 and 1.0")
        _require_finite(self.requested_at, "requested_at")
        if self.not_after is not None:
            _require_finite(self.not_after, "not_after")
            if self.not_after < self.requested_at:
                raise ValueError("not_after cannot precede requested_at")
        if self.lease_s is not None:
            _require_positive(self.lease_s, "lease_s")
        _require_non_negative(self.cooldown_s, "cooldown_s")
        if self.cooldown_key is not None:
            _require_identifier(self.cooldown_key, "cooldown_key")
        for resource in self.resources:
            _require_identifier(resource, "resource")
        if self.fallback_from is not None:
            _require_identifier(self.fallback_from, "fallback_from")


@dataclass(frozen=True, slots=True)
class ActiveLease:
    cue: PresentationCue
    started_at: float
    expires_at: float | None

    def __post_init__(self) -> None:
        _require_finite(self.started_at, "started_at")
        if self.expires_at is not None:
            _require_finite(self.expires_at, "expires_at")
            if self.expires_at < self.started_at:
                raise ValueError("expires_at cannot precede started_at")


class PresentationEventKind(StrEnum):
    STARTED = "started"
    STOPPED = "stopped"


class StopReason(StrEnum):
    PREEMPTED = "preempted"
    LEASE_EXPIRED = "lease_expired"
    CANCELLED = "cancelled"
    GENERATION_ADVANCED = "generation_advanced"
    DISCONNECTED = "disconnected"
    ADAPTER_FAILURE = "adapter_failure"


@dataclass(frozen=True, slots=True)
class PresentationEvent:
    sequence: int
    kind: PresentationEventKind
    cue: PresentationCue
    occurred_at: float
    reason: StopReason | None = None
    replaced_by: str | None = None


class ScheduleStatus(StrEnum):
    STARTED = "started"
    PENDING_AUDIO = "pending_audio"
    REJECTED_STALE_GENERATION = "rejected_stale_generation"
    REJECTED_FUTURE_GENERATION = "rejected_future_generation"
    REJECTED_EXPIRED = "rejected_expired"
    REJECTED_DUPLICATE = "rejected_duplicate"
    REJECTED_PRIORITY = "rejected_priority"
    REJECTED_COOLDOWN = "rejected_cooldown"
    REJECTED_RATE_LIMIT = "rejected_rate_limit"
    REJECTED_QUEUE_FULL = "rejected_queue_full"
    REJECTED_DISCONNECTED = "rejected_disconnected"


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    status: ScheduleStatus
    cue: PresentationCue
    events: tuple[PresentationEvent, ...] = ()
    detail: str | None = None


__all__ = [
    "ActiveLease",
    "AudioAnchor",
    "AudioTimelineBinding",
    "AudioTimelineMarker",
    "BlendCurve",
    "BlendMode",
    "BlendSpec",
    "CuePriority",
    "CueRequest",
    "CueSource",
    "PresentationCue",
    "PresentationEvent",
    "PresentationEventKind",
    "ScheduleResult",
    "ScheduleStatus",
    "StopReason",
    "priority_for_source",
]
