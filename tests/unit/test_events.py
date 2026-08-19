from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from eeveetuber.domain.events import (
    EventEnvelope,
    EventPriority,
    RetentionClass,
    TrustLabel,
    Visibility,
)


@dataclass(frozen=True)
class TypedPayload:
    text: str
    scores: list[int]

    def to_event_payload(self) -> dict[str, object]:
        return {"text": self.text, "scores": self.scores}


def test_event_is_deeply_immutable_and_detached_from_input() -> None:
    original = {"nested": {"items": [1, 2]}}
    event = EventEnvelope.create("transcript.final", original)

    original["nested"]["items"].append(3)  # type: ignore[index,union-attr]

    assert event.payload["nested"]["items"] == (1, 2)  # type: ignore[index]
    with pytest.raises(TypeError):
        event.payload["new"] = "value"  # type: ignore[index]
    with pytest.raises(TypeError):
        event.payload["nested"]["items"][0] = 7  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        event.sequence = 9  # type: ignore[misc]


def test_typed_payload_and_metadata_round_trip() -> None:
    event_id = uuid4()
    session_id = uuid4()
    occurred_at = datetime(2026, 8, 19, 12, 30, tzinfo=UTC)
    event = EventEnvelope.create(
        "agent.text_delta",
        TypedPayload("hello", [3, 5]),
        event_id=event_id,
        occurred_at=occurred_at,
        monotonic_at_ms=1234,
        session_id=session_id,
        actor_id="foreground-model",
        sequence=4,
        priority=EventPriority.HIGH,
        trust=TrustLabel.SYSTEM,
        visibility=Visibility.STREAM_SAFE,
        retention=RetentionClass.TRANSCRIPT,
    )

    assert event.correlation_id == event_id
    restored = EventEnvelope.from_dict(event.to_dict())
    assert restored == event
    assert restored.payload["scores"] == (3, 5)


def test_sequence_and_session_binding_return_copies() -> None:
    session_id = uuid4()
    original = EventEnvelope.create("audio.chunk", {"ordinal": 2})

    bound = original.for_session(session_id).with_sequence(11)

    assert original.session_id is None
    assert original.sequence is None
    assert bound.session_id == session_id
    assert bound.sequence == 11
    assert bound.event_id == original.event_id
    assert bound.for_session(session_id) is bound
    with pytest.raises(ValueError, match="event belongs to session"):
        bound.for_session(uuid4())


@pytest.mark.parametrize(
    "event_type",
    ["", "Transcript.Final", ".bad", "bad..type", "bad type", "bad/segment"],
)
def test_invalid_event_types_are_rejected(event_type: str) -> None:
    with pytest.raises(ValueError, match="event type"):
        EventEnvelope.create(event_type)


def test_invalid_payload_and_clock_metadata_are_rejected() -> None:
    with pytest.raises(TypeError, match="keys must be strings"):
        EventEnvelope.create("test.event", {1: "bad"})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="not JSON-compatible"):
        EventEnvelope.create("test.event", {"value": object()})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="non-finite"):
        EventEnvelope.create("test.event", {"value": float("nan")})
    with pytest.raises(ValueError, match="timezone-aware"):
        EventEnvelope.create("test.event", occurred_at=datetime(2026, 1, 1))
    with pytest.raises(ValueError, match="sequence"):
        EventEnvelope.create("test.event", sequence=-1)

