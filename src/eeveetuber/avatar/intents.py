"""Renderer-independent semantic intents for avatar performance.

These types are safe to expose to cognition and persona layers.  They deliberately
contain no renderer identifiers, Live2D parameter names, motion indices, or frame
timing.  Translation into avatar-specific resources belongs to
``AvatarCapabilityProfile`` and ``PerformanceDirector``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


def _require_unit_interval(value: float, field_name: str) -> None:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be finite and between 0.0 and 1.0")


def _require_signed_unit(value: float, field_name: str) -> None:
    if not math.isfinite(value) or not -1.0 <= value <= 1.0:
        raise ValueError(f"{field_name} must be finite and between -1.0 and 1.0")


class PresentationLayer(StrEnum):
    """Independently arbitrated avatar presentation layers."""

    AFFECT = "affect"
    GESTURE = "gesture"
    GAZE = "gaze"
    POSTURE = "posture"


class AffectLabel(StrEnum):
    NEUTRAL = "neutral"
    JOY = "joy"
    SADNESS = "sadness"
    ANGER = "anger"
    FEAR = "fear"
    SURPRISE = "surprise"
    DISGUST = "disgust"
    CALM = "calm"
    CONCERN = "concern"
    CONFUSION = "confusion"
    EXCITEMENT = "excitement"


class GestureKind(StrEnum):
    NEUTRAL = "neutral"
    NOD = "nod"
    SHAKE_HEAD = "shake_head"
    WAVE = "wave"
    SHRUG = "shrug"
    POINT_LEFT = "point_left"
    POINT_RIGHT = "point_right"
    CELEBRATE = "celebrate"
    THINK = "think"
    ACKNOWLEDGE = "acknowledge"


class GazeTarget(StrEnum):
    NEUTRAL = "neutral"
    USER = "user"
    CAMERA = "camera"
    SCREEN = "screen"
    LEFT = "left"
    RIGHT = "right"
    UP = "up"
    DOWN = "down"
    AWAY = "away"


class PostureKind(StrEnum):
    NEUTRAL = "neutral"
    RELAXED = "relaxed"
    ATTENTIVE = "attentive"
    OPEN = "open"
    CLOSED = "closed"
    LEAN_FORWARD = "lean_forward"
    LEAN_BACK = "lean_back"
    THINKING = "thinking"


@runtime_checkable
class SemanticIntent(Protocol):
    """Structural contract implemented by every semantic avatar intent."""

    @property
    def layer(self) -> PresentationLayer: ...

    @property
    def semantic_key(self) -> str: ...

    @property
    def intensity(self) -> float: ...


@dataclass(frozen=True, slots=True)
class AffectIntent:
    """Dimensional affect with an optional stable categorical hint.

    ``valence`` is in ``[-1, 1]`` and ``arousal``/``intensity`` are in
    ``[0, 1]``.  When a label is absent, ``effective_label`` provides a small,
    deterministic mapping suitable for capability lookup; it is not an AI
    classifier.
    """

    valence: float = 0.0
    arousal: float = 0.0
    label: AffectLabel | None = None
    intensity: float = 1.0

    def __post_init__(self) -> None:
        _require_signed_unit(self.valence, "valence")
        _require_unit_interval(self.arousal, "arousal")
        _require_unit_interval(self.intensity, "intensity")

    @property
    def layer(self) -> PresentationLayer:
        return PresentationLayer.AFFECT

    @property
    def effective_label(self) -> AffectLabel:
        if self.label is not None:
            return self.label
        if self.valence >= 0.2:
            return AffectLabel.EXCITEMENT if self.arousal >= 0.75 else AffectLabel.JOY
        if self.valence <= -0.2:
            return AffectLabel.ANGER if self.arousal >= 0.65 else AffectLabel.SADNESS
        if self.arousal >= 0.8:
            return AffectLabel.SURPRISE
        return AffectLabel.NEUTRAL

    @property
    def semantic_key(self) -> str:
        return f"{self.layer.value}.{self.effective_label.value}"


@dataclass(frozen=True, slots=True)
class GestureIntent:
    gesture: GestureKind
    intensity: float = 1.0

    def __post_init__(self) -> None:
        _require_unit_interval(self.intensity, "intensity")

    @property
    def layer(self) -> PresentationLayer:
        return PresentationLayer.GESTURE

    @property
    def semantic_key(self) -> str:
        return f"{self.layer.value}.{self.gesture.value}"


@dataclass(frozen=True, slots=True)
class GazeIntent:
    target: GazeTarget
    intensity: float = 1.0

    def __post_init__(self) -> None:
        _require_unit_interval(self.intensity, "intensity")

    @property
    def layer(self) -> PresentationLayer:
        return PresentationLayer.GAZE

    @property
    def semantic_key(self) -> str:
        return f"{self.layer.value}.{self.target.value}"


@dataclass(frozen=True, slots=True)
class PostureIntent:
    posture: PostureKind
    intensity: float = 1.0

    def __post_init__(self) -> None:
        _require_unit_interval(self.intensity, "intensity")

    @property
    def layer(self) -> PresentationLayer:
        return PresentationLayer.POSTURE

    @property
    def semantic_key(self) -> str:
        return f"{self.layer.value}.{self.posture.value}"


type CueIntent = AffectIntent | GestureIntent | GazeIntent | PostureIntent


__all__ = [
    "AffectIntent",
    "AffectLabel",
    "CueIntent",
    "GazeIntent",
    "GazeTarget",
    "GestureIntent",
    "GestureKind",
    "PostureIntent",
    "PostureKind",
    "PresentationLayer",
    "SemanticIntent",
]
