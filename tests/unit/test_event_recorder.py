from __future__ import annotations

from uuid import uuid4

import pytest

from eeveetuber.application.event_recorder import AsyncEventRecorder
from eeveetuber.domain.events import EventEnvelope, RetentionClass
from eeveetuber.storage import EventRecord


class RecordingSink:
    def __init__(self) -> None:
        self.records: list[EventRecord] = []

    def append(self, record: EventRecord) -> EventRecord:
        self.records.append(record)
        return record


@pytest.mark.asyncio
async def test_recorder_persists_ordered_envelopes_and_redacts_audio() -> None:
    session_id = uuid4()
    sink = RecordingSink()
    recorder = AsyncEventRecorder(sink)
    recorder.start()
    recorder.observe(
        EventEnvelope.create(
            "turn.requested",
            {"text": "private text", "generation": 1},
            session_id=session_id,
            sequence=4,
            retention=RetentionClass.TRANSCRIPT,
        )
    )
    recorder.observe(
        EventEnvelope.create(
            "speech.audio_chunk",
            {"audio_base64": "RkFLRQ==", "generation": 1, "chunk_index": 0},
            session_id=session_id,
            sequence=5,
            retention=RetentionClass.EPHEMERAL_MEDIA,
        )
    )

    stats = await recorder.close()

    assert stats.persisted == 2
    assert stats.dropped == 0
    assert [record.event_type for record in sink.records] == [
        "turn.requested",
        "speech.audio_chunk",
    ]
    first_envelope = sink.records[0].payload
    assert first_envelope["sequence"] == 4
    assert first_envelope["payload"] == {"text": "private text", "generation": 1}
    audio_payload = sink.records[1].payload["payload"]
    assert isinstance(audio_payload, dict)
    assert "audio_base64" not in audio_payload
    assert audio_payload["audio_redacted"] is True
    assert audio_payload["audio_base64_chars"] == 8


@pytest.mark.asyncio
async def test_partial_voice_transcripts_never_enter_the_event_journal() -> None:
    sink = RecordingSink()
    recorder = AsyncEventRecorder(sink)
    recorder.start()
    recorder.observe(
        EventEnvelope.create(
            "voice.transcript_partial",
            {"text": "abandoned private hypothesis", "revision": 0},
            sequence=1,
            retention=RetentionClass.EPHEMERAL_MEDIA,
        )
    )
    recorder.observe(
        EventEnvelope.create(
            "voice.transcript_partial",
            {"text": "mislabelled private hypothesis", "revision": 1},
            sequence=2,
            retention=RetentionClass.OPERATIONAL_TRACE,
        )
    )
    recorder.observe(
        EventEnvelope.create(
            "voice.transcript_final",
            {"text": "keep this final transcript"},
            sequence=3,
            retention=RetentionClass.TRANSCRIPT,
        )
    )

    stats = await recorder.close()

    assert stats.persisted == 1
    assert stats.dropped == 0
    assert [record.event_type for record in sink.records] == ["voice.transcript_final"]
    assert sink.records[0].payload["payload"] == {"text": "keep this final transcript"}


@pytest.mark.asyncio
async def test_transcript_event_displaces_operational_trace_when_queue_is_full() -> None:
    sink = RecordingSink()
    recorder = AsyncEventRecorder(sink, capacity=2)
    recorder.observe(EventEnvelope.create("trace.first", sequence=0))
    recorder.observe(EventEnvelope.create("trace.second", sequence=1))
    recorder.observe(
        EventEnvelope.create(
            "turn.requested",
            {"text": "keep me"},
            sequence=2,
            retention=RetentionClass.TRANSCRIPT,
        )
    )

    recorder.start()
    stats = await recorder.close()

    assert stats.dropped == 1
    assert [record.event_type for record in sink.records] == [
        "trace.second",
        "turn.requested",
    ]
