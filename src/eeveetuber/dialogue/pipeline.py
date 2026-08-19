"""Low-latency foreground model-to-segment-to-audio orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from eeveetuber.dialogue.assembler import IncrementalUtteranceAssembler
from eeveetuber.dialogue.ports import ModelProvider, SpeechSynthesizer
from eeveetuber.dialogue.types import (
    AudioChunk,
    DialogueRequest,
    DialogueStreamEvent,
    ModelCompleted,
    ModelStopReason,
    ModelTextDelta,
    SegmentAudioReady,
    SegmentReady,
    UtteranceCompleted,
    UtterancePlan,
    UtteranceSegment,
)
from eeveetuber.runtime.cancellation import CancellationToken


class DialogueCancelled(Exception):
    """The session generation changed before this run could publish more output."""


class DialogueProtocolError(Exception):
    """A provider violated the normalized stream contract."""


@dataclass(frozen=True, slots=True)
class _ProductionFinished:
    plan: UtterancePlan


@dataclass(frozen=True, slots=True)
class _StageFailed:
    error: BaseException


@dataclass(slots=True)
class _SynthesisJob:
    segment: UtteranceSegment
    audio: asyncio.Queue[_AudioItem]


@dataclass(frozen=True, slots=True)
class _SynthesisFinished:
    pass


type _ProductionItem = UtteranceSegment | _ProductionFinished | _StageFailed
type _OutputItem = _SynthesisJob | _ProductionFinished | _StageFailed
type _AudioItem = AudioChunk | _SynthesisFinished | _StageFailed


class DialoguePipeline:
    """Overlap bounded model and speech work while publishing in strict segment order.

    The model producer, synthesis dispatcher, and a limited set of synthesis tasks run
    independently. Per-job audio queues preserve backpressure, and only this generator publishes
    results, so externally visible segment and audio ordering remains deterministic.

    Generation validity is checked before every externally visible event. Provider cancellation
    reduces wasted work, while this check is the correctness boundary.
    """

    def __init__(
        self,
        model: ModelProvider,
        speech: SpeechSynthesizer,
        *,
        is_generation_current: Callable[[int], bool],
        max_segment_chars: int = 220,
        segment_queue_capacity: int = 4,
        max_concurrent_synthesis: int = 1,
        audio_queue_capacity: int = 4,
    ) -> None:
        if segment_queue_capacity <= 0:
            raise ValueError("segment_queue_capacity must be positive")
        if max_concurrent_synthesis <= 0:
            raise ValueError("max_concurrent_synthesis must be positive")
        if audio_queue_capacity <= 0:
            raise ValueError("audio_queue_capacity must be positive")
        self._model = model
        self._speech = speech
        self._is_generation_current = is_generation_current
        self._max_segment_chars = max_segment_chars
        self._segment_queue_capacity = segment_queue_capacity
        self._max_concurrent_synthesis = max_concurrent_synthesis
        self._audio_queue_capacity = audio_queue_capacity

    async def run(
        self,
        request: DialogueRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[DialogueStreamEvent]:
        self._check_active(request.generation, cancellation)
        segments: asyncio.Queue[_ProductionItem] = asyncio.Queue(
            maxsize=self._segment_queue_capacity
        )
        output: asyncio.Queue[_OutputItem] = asyncio.Queue(
            maxsize=self._max_concurrent_synthesis + 1
        )
        synthesis_slots = asyncio.Semaphore(self._max_concurrent_synthesis)
        synthesis_tasks: set[asyncio.Task[None]] = set()
        stopping = asyncio.Event()

        producer = asyncio.create_task(
            self._produce_segments(request, segments, cancellation, stopping),
            name=f"dialogue-model-{request.turn_id}",
        )
        dispatcher = asyncio.create_task(
            self._dispatch_synthesis(
                segments,
                output,
                synthesis_slots,
                synthesis_tasks,
                cancellation,
                stopping,
            ),
            name=f"dialogue-speech-dispatch-{request.turn_id}",
        )

        try:
            while True:
                item = await _queue_get_or_cancel(output, cancellation)
                if isinstance(item, _StageFailed):
                    raise item.error
                if isinstance(item, _ProductionFinished):
                    await _await_tasks(producer, dispatcher, *synthesis_tasks)
                    self._check_active(request.generation, cancellation)
                    yield UtteranceCompleted(
                        turn_id=request.turn_id,
                        generation=request.generation,
                        plan=item.plan,
                    )
                    return

                self._check_active(request.generation, cancellation)
                yield SegmentReady(
                    turn_id=request.turn_id,
                    generation=request.generation,
                    segment=item.segment,
                )
                while True:
                    audio_item = await _queue_get_or_cancel(item.audio, cancellation)
                    if isinstance(audio_item, _StageFailed):
                        raise audio_item.error
                    if isinstance(audio_item, _SynthesisFinished):
                        break
                    self._check_active(request.generation, cancellation)
                    yield SegmentAudioReady(
                        turn_id=request.turn_id,
                        generation=request.generation,
                        segment=item.segment,
                        chunk=audio_item,
                    )
        finally:
            stopping.set()
            await _cancel_tasks(producer, dispatcher, *synthesis_tasks)

    async def _produce_segments(
        self,
        request: DialogueRequest,
        output: asyncio.Queue[_ProductionItem],
        cancellation: CancellationToken | None,
        stopping: asyncio.Event,
    ) -> None:
        assembler = IncrementalUtteranceAssembler(max_segment_chars=self._max_segment_chars)
        completion: ModelCompleted | None = None
        try:
            async for model_event in self._model.stream(request, cancellation=cancellation):
                self._check_active(request.generation, cancellation)
                if isinstance(model_event, ModelTextDelta):
                    for segment in assembler.push(model_event.text):
                        await output.put(segment)
                elif isinstance(model_event, ModelCompleted):
                    if completion is not None:
                        raise DialogueProtocolError("model emitted more than one completion event")
                    completion = model_event
                else:
                    raise DialogueProtocolError(f"unsupported model event: {type(model_event)!r}")

            for segment in assembler.finish():
                await output.put(segment)

            completion = completion or ModelCompleted(stop_reason=ModelStopReason.COMPLETE)
            await output.put(
                _ProductionFinished(
                    UtterancePlan(
                        turn_id=request.turn_id,
                        generation=request.generation,
                        segments=assembler.segments,
                        stop_reason=completion.stop_reason,
                        input_tokens=completion.input_tokens,
                        output_tokens=completion.output_tokens,
                    )
                )
            )
        except asyncio.CancelledError as error:
            if stopping.is_set():
                raise
            await output.put(_StageFailed(error))
        except Exception as error:
            await output.put(_StageFailed(error))

    async def _dispatch_synthesis(
        self,
        segments: asyncio.Queue[_ProductionItem],
        output: asyncio.Queue[_OutputItem],
        synthesis_slots: asyncio.Semaphore,
        synthesis_tasks: set[asyncio.Task[None]],
        cancellation: CancellationToken | None,
        stopping: asyncio.Event,
    ) -> None:
        try:
            while True:
                item = await segments.get()
                if not isinstance(item, UtteranceSegment):
                    await output.put(item)
                    return

                await synthesis_slots.acquire()
                job = _SynthesisJob(
                    segment=item,
                    audio=asyncio.Queue(maxsize=self._audio_queue_capacity),
                )
                await output.put(job)
                _discard_finished_tasks(synthesis_tasks)
                task = asyncio.create_task(
                    self._synthesize(job, synthesis_slots, cancellation, stopping),
                    name=f"dialogue-tts-{item.sequence}-{item.segment_id}",
                )
                synthesis_tasks.add(task)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await output.put(_StageFailed(error))

    async def _synthesize(
        self,
        job: _SynthesisJob,
        synthesis_slots: asyncio.Semaphore,
        cancellation: CancellationToken | None,
        stopping: asyncio.Event,
    ) -> None:
        expected_chunk_index = 0
        received_final = False
        try:
            async for chunk in self._speech.synthesize(
                job.segment,
                cancellation=cancellation,
            ):
                if received_final:
                    raise DialogueProtocolError("speech emitted audio after its final chunk")
                if chunk.segment_id != job.segment.segment_id:
                    raise DialogueProtocolError(
                        "speech audio segment_id does not match its utterance segment"
                    )
                if chunk.sequence != job.segment.sequence:
                    raise DialogueProtocolError(
                        "speech audio sequence does not match its utterance segment"
                    )
                if chunk.chunk_index != expected_chunk_index:
                    raise DialogueProtocolError(
                        "speech audio chunk_index must be contiguous and start at zero"
                    )
                expected_chunk_index += 1
                received_final = chunk.is_final
                await job.audio.put(chunk)
            if expected_chunk_index == 0:
                raise DialogueProtocolError("speech emitted no audio chunks")
            if not received_final:
                raise DialogueProtocolError("speech audio ended without a final chunk")
            await job.audio.put(_SynthesisFinished())
        except asyncio.CancelledError as error:
            if stopping.is_set():
                raise
            await job.audio.put(_StageFailed(error))
        except Exception as error:
            await job.audio.put(_StageFailed(error))
        finally:
            synthesis_slots.release()

    def _check_active(
        self,
        generation: int,
        cancellation: CancellationToken | None,
    ) -> None:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if not self._is_generation_current(generation):
            raise DialogueCancelled(f"dialogue generation {generation} is no longer current")


async def _queue_get_or_cancel[ItemT](
    queue: asyncio.Queue[ItemT],
    cancellation: CancellationToken | None,
) -> ItemT:
    if cancellation is None:
        return await queue.get()
    cancellation.raise_if_cancelled()
    item = asyncio.create_task(queue.get())
    cancelled = asyncio.create_task(cancellation.wait_cancelled())
    try:
        done, _pending = await asyncio.wait(
            {item, cancelled},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancelled in done:
            raise asyncio.CancelledError(cancelled.result())
        return item.result()
    finally:
        await _cancel_tasks(item, cancelled)


def _discard_finished_tasks(tasks: set[asyncio.Task[None]]) -> None:
    for task in tuple(tasks):
        if not task.done():
            continue
        tasks.discard(task)
        with suppress(asyncio.CancelledError):
            task.result()


async def _await_tasks(*tasks: asyncio.Task[None]) -> None:
    if tasks:
        await asyncio.gather(*tasks)


async def _cancel_tasks(*tasks: asyncio.Task[Any]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
