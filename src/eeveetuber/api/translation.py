"""Translate immutable domain events into transport DTOs."""

from __future__ import annotations

from eeveetuber.api.protocol import ServerMessage
from eeveetuber.domain.events import EventEnvelope


def event_to_server_message(event: EventEnvelope, *, generation: int) -> ServerMessage:
    if event.session_id is None:
        raise ValueError("transport output event must be scoped to a session")
    if event.sequence is None:
        raise ValueError("transport output event must have an owner-assigned sequence")
    primitive = event.to_dict()
    payload = primitive["payload"]
    if not isinstance(payload, dict):  # defensive: EventEnvelope guarantees a mapping
        raise TypeError("event payload must translate to an object")
    return ServerMessage(
        message_id=event.event_id,
        type=event.type,
        occurred_at=event.occurred_at,
        session_id=event.session_id,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        sequence=event.sequence,
        generation=generation,
        data=payload,
    )
