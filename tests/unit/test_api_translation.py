import base64
from uuid import uuid4

import pytest

from eeveetuber.api.audio_frames import AudioFrameFlags
from eeveetuber.api.protocol import StatusCode
from eeveetuber.api.translation import (
    event_to_audio_frame,
    event_to_server_message,
    event_to_status_message,
)
from eeveetuber.domain.events import EventEnvelope


def test_translates_stamped_session_event() -> None:
    session_id = uuid4()
    event = EventEnvelope.create(
        "utterance.segment_ready",
        {"text": "hello"},
        session_id=session_id,
        sequence=4,
    )

    message = event_to_server_message(event, generation=2)

    assert message.type == event.type
    assert message.session_id == session_id
    assert message.sequence == 4
    assert message.generation == 2
    assert message.data == {"text": "hello"}


def test_rejects_unstamped_event() -> None:
    with pytest.raises(ValueError, match="scoped"):
        event_to_server_message(EventEnvelope.create("test.event"), generation=0)


def test_translates_audio_event_to_binary_frame_metadata() -> None:
    session_id = uuid4()
    turn_id = uuid4()
    segment_id = uuid4()
    event = EventEnvelope.create(
        "speech.audio_chunk",
        {
            "turn_id": str(turn_id),
            "segment_id": str(segment_id),
            "sequence": 2,
            "chunk_index": 3,
            "media_type": "audio/mpeg",
            "sample_rate_hz": 24_000,
            "is_final": True,
            "duration_ms": 120,
            "audio_base64": base64.b64encode(b"audio").decode("ascii"),
        },
        session_id=session_id,
        sequence=9,
    )

    frame = event_to_audio_frame(event, generation=4)

    assert frame.session_id == session_id
    assert frame.turn_id == turn_id
    assert frame.segment_id == segment_id
    assert frame.event_sequence == 9
    assert frame.audio == b"audio"
    assert frame.flags & AudioFrameFlags.FINAL_CHUNK


def test_projects_known_lifecycle_event_to_typed_status() -> None:
    event = EventEnvelope.create("turn.accepted", session_id=uuid4(), sequence=5)

    status = event_to_status_message(event, generation=3)

    assert status is not None
    assert status.status is StatusCode.PROCESSING
    assert event_to_status_message(
        EventEnvelope.create("context.snapshot_published", session_id=uuid4(), sequence=1),
        generation=1,
    ) is None
