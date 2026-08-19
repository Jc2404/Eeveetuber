import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest

from eeveetuber.adapters.fake import FakeModelProvider, FakeSpeechSynthesizer
from eeveetuber.dialogue.pipeline import (
    DialogueCancelled,
    DialoguePipeline,
    DialogueProtocolError,
)
from eeveetuber.dialogue.types import (
    AudioChunk,
    DialogueRequest,
    ModelCompleted,
    ModelStreamEvent,
    ModelTextDelta,
    SegmentAudioReady,
    SegmentReady,
    UtteranceCompleted,
    UtteranceSegment,
)
from eeveetuber.runtime.cancellation import CancellationSource, CancellationToken


def _audio(
    segment: UtteranceSegment,
    *,
    segment_id: UUID | None = None,
    sequence: int | None = None,
    chunk_index: int = 0,
    is_final: bool = True,
) -> AudioChunk:
    return AudioChunk(
        segment_id=segment.segment_id if segment_id is None else segment_id,
        sequence=segment.sequence if sequence is None else sequence,
        chunk_index=chunk_index,
        audio=f"audio:{segment.speakable_text}".encode(),
        media_type="audio/test",
        sample_rate_hz=24_000,
        is_final=is_final,
    )


async def _collect(
    stream: AsyncIterator[SegmentReady | SegmentAudioReady | UtteranceCompleted],
) -> list[SegmentReady | SegmentAudioReady | UtteranceCompleted]:
    return [event async for event in stream]


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


class _ModelThatContinuesDuringSpeech:
    def __init__(self, speech_started: asyncio.Event) -> None:
        self.speech_started = speech_started
        self.continued = asyncio.Event()

    async def stream(
        self,
        _request: DialogueRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        yield ModelTextDelta("First.")
        await self.speech_started.wait()
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        self.continued.set()
        yield ModelTextDelta(" Second.")
        yield ModelCompleted()


class _SpeechThatWaitsForModel:
    def __init__(self, speech_started: asyncio.Event, model_continued: asyncio.Event) -> None:
        self.speech_started = speech_started
        self.model_continued = model_continued

    async def synthesize(
        self,
        segment: UtteranceSegment,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.speech_started.set()
        await self.model_continued.wait()
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        yield _audio(segment)


@pytest.mark.asyncio
async def test_model_stream_continues_while_first_segment_is_synthesized() -> None:
    speech_started = asyncio.Event()
    model = _ModelThatContinuesDuringSpeech(speech_started)
    speech = _SpeechThatWaitsForModel(speech_started, model.continued)
    pipeline = DialoguePipeline(
        model,
        speech,
        is_generation_current=lambda generation: generation == 1,
    )
    request = DialogueRequest(
        session_id=uuid4(),
        turn_id=uuid4(),
        generation=1,
        user_text="continue while speaking",
        system_context="persona",
    )

    events = await asyncio.wait_for(_collect(pipeline.run(request)), timeout=1)

    assert model.continued.is_set()
    assert [event.segment.sequence for event in events if isinstance(event, SegmentReady)] == [
        0,
        1,
    ]


class _OutOfOrderSpeech:
    def __init__(self) -> None:
        self.second_started = asyncio.Event()
        self.active = 0
        self.max_active = 0

    async def synthesize(
        self,
        segment: UtteranceSegment,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if segment.sequence == 0:
                await self.second_started.wait()
            else:
                self.second_started.set()
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            yield _audio(segment)
        finally:
            self.active -= 1


@pytest.mark.asyncio
async def test_synthesizes_ahead_but_publishes_segments_and_audio_in_order() -> None:
    speech = _OutOfOrderSpeech()
    pipeline = DialoguePipeline(
        FakeModelProvider("First. Second.", chunk_chars=100),
        speech,
        is_generation_current=lambda generation: generation == 2,
        max_concurrent_synthesis=2,
    )
    request = DialogueRequest(
        session_id=uuid4(),
        turn_id=uuid4(),
        generation=2,
        user_text="two sentences",
        system_context="persona",
    )

    events = await asyncio.wait_for(_collect(pipeline.run(request)), timeout=1)

    visible_order = [
        ("segment" if isinstance(event, SegmentReady) else "audio", event.segment.sequence)
        for event in events
        if isinstance(event, SegmentReady | SegmentAudioReady)
    ]
    assert speech.max_active == 2
    assert visible_order == [
        ("segment", 0),
        ("audio", 0),
        ("segment", 1),
        ("audio", 1),
    ]


class _CountingModel:
    def __init__(self, count: int) -> None:
        self.count = count
        self.produced = 0

    async def stream(
        self,
        _request: DialogueRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        for sequence in range(self.count):
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            self.produced += 1
            yield ModelTextDelta(f"Sentence {sequence}. ")
        yield ModelCompleted()


class _GatedSpeech:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def synthesize(
        self,
        segment: UtteranceSegment,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AudioChunk]:
        self.started.set()
        await self.release.wait()
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        yield _audio(segment)


@pytest.mark.asyncio
async def test_bounded_queues_apply_backpressure_to_fast_model_streams() -> None:
    model = _CountingModel(count=20)
    speech = _GatedSpeech()
    pipeline = DialoguePipeline(
        model,
        speech,
        is_generation_current=lambda generation: generation == 5,
        segment_queue_capacity=1,
        max_concurrent_synthesis=1,
        audio_queue_capacity=1,
    )
    request = DialogueRequest(
        session_id=uuid4(),
        turn_id=uuid4(),
        generation=5,
        user_text="many sentences",
        system_context="persona",
    )
    stream = pipeline.run(request)

    first = await asyncio.wait_for(anext(stream), timeout=1)
    assert isinstance(first, SegmentReady)
    await asyncio.wait_for(speech.started.wait(), timeout=1)
    await asyncio.sleep(0)

    assert model.produced < model.count

    speech.release.set()
    remaining = await asyncio.wait_for(_collect(stream), timeout=1)
    assert isinstance(remaining[-1], UtteranceCompleted)


class _EndlessModel:
    def __init__(self) -> None:
        self.closed = asyncio.Event()

    async def stream(
        self,
        _request: DialogueRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        try:
            yield ModelTextDelta("First.")
            await asyncio.Event().wait()
        finally:
            self.closed.set()


@pytest.mark.asyncio
async def test_closing_output_stream_cancels_model_producer() -> None:
    model = _EndlessModel()
    pipeline = DialoguePipeline(
        model,
        FakeSpeechSynthesizer(),
        is_generation_current=lambda generation: generation == 9,
    )
    request = DialogueRequest(
        session_id=uuid4(),
        turn_id=uuid4(),
        generation=9,
        user_text="start",
        system_context="persona",
    )
    stream = pipeline.run(request)

    first = await asyncio.wait_for(anext(stream), timeout=1)
    assert isinstance(first, SegmentReady)
    await stream.aclose()

    assert model.closed.is_set()


class _NeverFinishingSpeech:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = asyncio.Event()

    async def synthesize(
        self,
        _segment: UtteranceSegment,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AudioChunk]:
        try:
            self.started.set()
            await asyncio.Event().wait()
            if cancellation is not None:
                cancellation.raise_if_cancelled()
            if False:  # pragma: no cover - makes this an async generator
                yield _audio(_segment)
        finally:
            self.closed.set()


@pytest.mark.asyncio
async def test_cancellation_interrupts_waiting_pipeline_and_closes_speech() -> None:
    cancellation = CancellationSource(initial_generation=11)
    token = cancellation.token()
    speech = _NeverFinishingSpeech()
    pipeline = DialoguePipeline(
        FakeModelProvider("First."),
        speech,
        is_generation_current=lambda generation: (
            generation == token.generation.value and not token.cancelled
        ),
    )
    request = DialogueRequest(
        session_id=uuid4(),
        turn_id=uuid4(),
        generation=11,
        user_text="start",
        system_context="persona",
    )
    stream = pipeline.run(request, cancellation=token)

    first = await asyncio.wait_for(anext(stream), timeout=1)
    assert isinstance(first, SegmentReady)
    await asyncio.wait_for(speech.started.wait(), timeout=1)
    cancellation.cancel_current("barge in")

    with pytest.raises(asyncio.CancelledError, match="barge in"):
        await asyncio.wait_for(anext(stream), timeout=1)
    assert speech.closed.is_set()


class _InvalidSpeech:
    def __init__(self, failure: str) -> None:
        self.failure = failure

    async def synthesize(
        self,
        segment: UtteranceSegment,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AudioChunk]:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if self.failure == "empty":
            return
        if self.failure == "segment_id":
            yield _audio(segment, segment_id=uuid4())
            return
        if self.failure == "sequence":
            yield _audio(segment, sequence=segment.sequence + 1)
            return
        if self.failure == "chunk_index":
            yield _audio(segment, chunk_index=1)
            return
        if self.failure == "missing_final":
            yield _audio(segment, is_final=False)
            return
        if self.failure == "after_final":
            yield _audio(segment)
            yield _audio(segment, chunk_index=1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("empty", "no audio chunks"),
        ("segment_id", "segment_id does not match"),
        ("sequence", "sequence does not match"),
        ("chunk_index", "chunk_index must be contiguous"),
        ("missing_final", "without a final chunk"),
        ("after_final", "after its final chunk"),
    ],
)
async def test_rejects_invalid_speech_chunk_streams(failure: str, message: str) -> None:
    pipeline = DialoguePipeline(
        FakeModelProvider("First."),
        _InvalidSpeech(failure),
        is_generation_current=lambda generation: generation == 13,
    )
    request = DialogueRequest(
        session_id=uuid4(),
        turn_id=uuid4(),
        generation=13,
        user_text="start",
        system_context="persona",
    )

    with pytest.raises(DialogueProtocolError, match=message):
        await asyncio.wait_for(_collect(pipeline.run(request)), timeout=1)
