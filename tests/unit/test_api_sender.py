from __future__ import annotations

import base64
import json
from collections import deque
from types import SimpleNamespace
from uuid import uuid4

import pytest

from eeveetuber.api.app import _send_session_output
from eeveetuber.api.audio_frames import decode_audio_frame
from eeveetuber.domain import EventEnvelope
from eeveetuber.runtime import CancellationGeneration, MailboxClosed


class CapturingWebSocket:
    def __init__(self) -> None:
        self.text: list[str] = []
        self.binary: list[bytes] = []

    async def send_text(self, message: str) -> None:
        self.text.append(message)

    async def send_bytes(self, message: bytes) -> None:
        self.binary.append(message)


class QueuedSession:
    def __init__(self, events: list[EventEnvelope], *, generation: int) -> None:
        self._events = deque(events)
        self.actor = SimpleNamespace(current_generation=CancellationGeneration(generation))

    async def receive_output(self) -> EventEnvelope:
        if self._events:
            return self._events.popleft()
        raise MailboxClosed


def _event(event_type: str, generation: int, sequence: int) -> EventEnvelope:
    return EventEnvelope.create(
        event_type,
        {"generation": generation},
        session_id=uuid4(),
        sequence=sequence,
    )


def _audio_event(generation: int, sequence: int) -> EventEnvelope:
    return EventEnvelope.create(
        "speech.audio_chunk",
        {
            "turn_id": str(uuid4()),
            "generation": generation,
            "segment_id": str(uuid4()),
            "sequence": 0,
            "chunk_index": 0,
            "media_type": "audio/mpeg",
            "sample_rate_hz": 24_000,
            "is_final": True,
            "duration_ms": 10,
            "audio_base64": base64.b64encode(b"audio").decode("ascii"),
        },
        session_id=uuid4(),
        sequence=sequence,
    )


@pytest.mark.parametrize("binary_audio", [False, True])
async def test_sender_drops_stale_utterance_and_audio_but_keeps_operational_events(
    binary_audio: bool,
) -> None:
    session_id = uuid4()
    events = [
        _event("utterance.segment_ready", 1, 1),
        _audio_event(1, 2),
        _event("utterance.completed", 1, 3),
        EventEnvelope.create(
            "context.snapshot_published",
            {"generation": 1, "snapshot_id": "old-but-observable"},
            session_id=session_id,
            sequence=4,
        ),
        EventEnvelope.create(
            "speech.cancelled",
            {"generation": 2},
            session_id=session_id,
            sequence=5,
        ),
        EventEnvelope.create(
            "utterance.segment_ready",
            {"generation": 2, "display_text": "current"},
            session_id=session_id,
            sequence=6,
        ),
        EventEnvelope.create(
            "speech.audio_chunk",
            {
                **dict(_audio_event(2, 7).payload),
                "generation": 2,
            },
            session_id=session_id,
            sequence=7,
        ),
    ]
    websocket = CapturingWebSocket()

    await _send_session_output(  # type: ignore[arg-type]
        websocket,
        QueuedSession(events, generation=2),  # type: ignore[arg-type]
        binary_audio=binary_audio,
    )

    messages = [json.loads(raw) for raw in websocket.text]
    event_messages = [message for message in messages if message["type"] != "session.status"]
    assert [message["type"] for message in event_messages] == [
        "context.snapshot_published",
        "speech.cancelled",
        "utterance.segment_ready",
    ] + ([] if binary_audio else ["speech.audio_chunk"])
    assert event_messages[0]["generation"] == 1
    if binary_audio:
        assert len(websocket.binary) == 1
        assert decode_audio_frame(websocket.binary[0]).generation == 2
    else:
        assert not websocket.binary
        assert event_messages[-1]["generation"] == 2
