"""Framework-independent domain contracts for Eeveetuber."""

from eeveetuber.domain.events import (
    EventEnvelope,
    EventPayload,
    EventPriority,
    RetentionClass,
    TrustLabel,
    Visibility,
)
from eeveetuber.domain.interaction import (
    InteractionState,
    InteractionStateMachine,
    InteractionTransition,
    InvalidInteractionTransition,
)

__all__ = [
    "EventEnvelope",
    "EventPayload",
    "EventPriority",
    "InteractionState",
    "InteractionStateMachine",
    "InteractionTransition",
    "InvalidInteractionTransition",
    "RetentionClass",
    "TrustLabel",
    "Visibility",
]
