from uuid import uuid4

import pytest

from eeveetuber.api.translation import event_to_server_message
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

