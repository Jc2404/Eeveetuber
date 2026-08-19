from uuid import uuid4

import pytest

from eeveetuber.adapters.fake import FakeModelProvider, FakeSpeechSynthesizer
from eeveetuber.dialogue.pipeline import DialogueCancelled, DialoguePipeline
from eeveetuber.dialogue.types import (
    DialogueRequest,
    SegmentAudioReady,
    SegmentReady,
    UtteranceCompleted,
)


@pytest.mark.asyncio
async def test_streams_segment_audio_before_complete_plan() -> None:
    current_generation = 3
    model = FakeModelProvider("First sentence. Second sentence!", chunk_chars=4)
    speech = FakeSpeechSynthesizer()
    pipeline = DialoguePipeline(
        model,
        speech,
        is_generation_current=lambda generation: generation == current_generation,
    )
    request = DialogueRequest(
        session_id=uuid4(),
        turn_id=uuid4(),
        generation=current_generation,
        user_text="Say two things",
        system_context="You are a concise character.",
    )

    events = [event async for event in pipeline.run(request)]

    assert isinstance(events[0], SegmentReady)
    assert isinstance(events[1], SegmentAudioReady)
    assert isinstance(events[-1], UtteranceCompleted)
    assert [segment.speakable_text for segment in events[-1].plan.segments] == [
        "First sentence.",
        "Second sentence!",
    ]
    assert len(model.requests) == 1


@pytest.mark.asyncio
async def test_rejects_audio_after_generation_changes() -> None:
    generation = 7
    is_current = True
    pipeline = DialoguePipeline(
        FakeModelProvider("A complete sentence."),
        FakeSpeechSynthesizer(),
        is_generation_current=lambda candidate: candidate == generation and is_current,
    )
    request = DialogueRequest(
        session_id=uuid4(),
        turn_id=uuid4(),
        generation=generation,
        user_text="hello",
        system_context="persona",
    )
    stream = pipeline.run(request)

    first = await anext(stream)
    assert isinstance(first, SegmentReady)
    is_current = False

    with pytest.raises(DialogueCancelled):
        await anext(stream)

