from __future__ import annotations

import asyncio
import struct
from uuid import UUID

import pytest

from eeveetuber.adapters.fake import FakeSpeechRecognizer
from eeveetuber.media import (
    AsrFinal,
    AsrPartial,
    PcmFormat,
    PcmFrame,
    PcmUtterance,
    UtteranceEndReason,
)
from eeveetuber.runtime import CancellationSource

STREAM_ID = UUID("00000000-0000-0000-0000-000000000030")
UTTERANCE_ID = UUID("00000000-0000-0000-0000-000000000031")


def _utterance() -> PcmUtterance:
    pcm_format = PcmFormat(16_000)
    frame = PcmFrame(
        stream_id=STREAM_ID,
        sequence=0,
        captured_at_monotonic_ns=100,
        format=pcm_format,
        pcm=struct.pack("<160h", *([500] * 160)),
    )
    return PcmUtterance(
        utterance_id=UTTERANCE_ID,
        stream_id=STREAM_ID,
        format=pcm_format,
        frames=(frame,),
        speech_started_at_monotonic_ns=100,
        speech_ended_at_monotonic_ns=frame.end_monotonic_ns,
        end_reason=UtteranceEndReason.STREAM_ENDED,
    )


@pytest.mark.asyncio
async def test_fake_asr_yields_optional_partials_then_exactly_one_final() -> None:
    recognizer = FakeSpeechRecognizer(
        "hello Eevee",
        partials=("hel", "hello"),
        language="en",
        confidence=0.9,
    )

    events = [event async for event in recognizer.recognize(_utterance())]

    assert [type(event) for event in events] == [AsrPartial, AsrPartial, AsrFinal]
    assert [event.text for event in events] == ["hel", "hello", "hello Eevee"]
    assert events[-1].utterance_id == UTTERANCE_ID
    assert len(recognizer.requests) == 1
    record = recognizer.requests[0]
    assert record.byte_count == 320
    assert record.frame_count == 1
    assert not hasattr(record, "pcm")
    assert not hasattr(record, "frames")


@pytest.mark.asyncio
async def test_fake_asr_callable_is_deterministic_and_receives_bounded_utterance() -> None:
    recognizer = FakeSpeechRecognizer(
        lambda utterance: f"received {utterance.byte_count} bytes",
        language=None,
        confidence=None,
    )

    first = [event async for event in recognizer.recognize(_utterance())]
    second = [event async for event in recognizer.recognize(_utterance())]

    assert first == second == [AsrFinal(UTTERANCE_ID, "received 320 bytes")]


@pytest.mark.asyncio
async def test_fake_asr_honors_cancellation_before_emitting_late_results() -> None:
    source = CancellationSource()
    token = source.token()
    recognizer = FakeSpeechRecognizer("too late", delay_seconds=0.01)

    stream = recognizer.recognize(_utterance(), cancellation=token)
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    source.cancel_current("barge-in")

    with pytest.raises(asyncio.CancelledError, match="barge-in"):
        await pending
