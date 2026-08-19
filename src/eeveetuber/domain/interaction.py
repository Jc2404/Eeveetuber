"""Validated interaction states owned by a single session actor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class InteractionState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    WAITING_APPROVAL = "waiting_approval"
    INTERRUPTING = "interrupting"
    DEGRADED = "degraded"


_TRANSITIONS: Final = MappingProxyType(
    {
        InteractionState.IDLE: frozenset(
            {InteractionState.LISTENING, InteractionState.PROCESSING, InteractionState.DEGRADED}
        ),
        InteractionState.LISTENING: frozenset(
            {
                InteractionState.IDLE,
                InteractionState.PROCESSING,
                InteractionState.INTERRUPTING,
                InteractionState.DEGRADED,
            }
        ),
        InteractionState.PROCESSING: frozenset(
            {
                InteractionState.IDLE,
                InteractionState.SPEAKING,
                InteractionState.WAITING_APPROVAL,
                InteractionState.INTERRUPTING,
                InteractionState.DEGRADED,
            }
        ),
        InteractionState.SPEAKING: frozenset(
            {
                InteractionState.IDLE,
                InteractionState.INTERRUPTING,
                InteractionState.DEGRADED,
            }
        ),
        InteractionState.WAITING_APPROVAL: frozenset(
            {
                InteractionState.IDLE,
                InteractionState.PROCESSING,
                InteractionState.INTERRUPTING,
                InteractionState.DEGRADED,
            }
        ),
        InteractionState.INTERRUPTING: frozenset(
            {
                InteractionState.IDLE,
                InteractionState.LISTENING,
                InteractionState.PROCESSING,
                InteractionState.DEGRADED,
            }
        ),
        InteractionState.DEGRADED: frozenset(
            {InteractionState.IDLE, InteractionState.LISTENING}
        ),
    }
)


class InvalidInteractionTransition(ValueError):
    """Raised when a component attempts to bypass the turn-state contract."""

    def __init__(self, previous: InteractionState, requested: InteractionState) -> None:
        self.previous = previous
        self.requested = requested
        super().__init__(f"illegal interaction transition: {previous.value} -> {requested.value}")


@dataclass(frozen=True, slots=True)
class InteractionTransition:
    previous: InteractionState
    current: InteractionState
    revision: int
    reason: str


class InteractionStateMachine:
    """Small deterministic state machine; its owning actor provides serialization."""

    __slots__ = ("_revision", "_state")

    def __init__(self, initial: InteractionState = InteractionState.IDLE) -> None:
        self._state = initial
        self._revision = 0

    @property
    def state(self) -> InteractionState:
        return self._state

    @property
    def revision(self) -> int:
        return self._revision

    @staticmethod
    def allowed_from(state: InteractionState) -> frozenset[InteractionState]:
        return _TRANSITIONS[state]

    def can_transition(self, requested: InteractionState) -> bool:
        return requested in _TRANSITIONS[self._state]

    def transition(self, requested: InteractionState, *, reason: str) -> InteractionTransition:
        if not reason.strip():
            raise ValueError("interaction transitions require a non-blank reason")
        previous = self._state
        if requested not in _TRANSITIONS[previous]:
            raise InvalidInteractionTransition(previous, requested)
        self._state = requested
        self._revision += 1
        return InteractionTransition(
            previous=previous,
            current=requested,
            revision=self._revision,
            reason=reason,
        )
