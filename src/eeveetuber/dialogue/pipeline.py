"""Low-latency foreground model-to-segment-to-audio orchestration."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from eeveetuber.dialogue.assembler import IncrementalUtteranceAssembler
from eeveetuber.dialogue.ports import ModelProvider, SpeechSynthesizer
from eeveetuber.dialogue.types import (
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


class DialogueCancelled(Exception):
    """The session generation changed before this run could publish more output."""


class DialogueProtocolError(Exception):
    """A provider violated the normalized stream contract."""


class DialoguePipeline:
    """Produce audio as soon as each text segment validates.

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
    ) -> None:
        self._model = model
        self._speech = speech
        self._is_generation_current = is_generation_current
        self._max_segment_chars = max_segment_chars

    async def run(self, request: DialogueRequest) -> AsyncIterator[DialogueStreamEvent]:
        assembler = IncrementalUtteranceAssembler(max_segment_chars=self._max_segment_chars)
        completion: ModelCompleted | None = None

        async for model_event in self._model.stream(request):
            self._check_generation(request.generation)
            if isinstance(model_event, ModelTextDelta):
                for segment in assembler.push(model_event.text):
                    async for event in self._emit_segment(request, segment):
                        yield event
            elif isinstance(model_event, ModelCompleted):
                if completion is not None:
                    raise DialogueProtocolError("model emitted more than one completion event")
                completion = model_event
            else:
                raise DialogueProtocolError(f"unsupported model event: {type(model_event)!r}")

        for segment in assembler.finish():
            async for event in self._emit_segment(request, segment):
                yield event

        self._check_generation(request.generation)
        completion = completion or ModelCompleted(stop_reason=ModelStopReason.COMPLETE)
        plan = UtterancePlan(
            turn_id=request.turn_id,
            generation=request.generation,
            segments=assembler.segments,
            stop_reason=completion.stop_reason,
        )
        yield UtteranceCompleted(
            turn_id=request.turn_id,
            generation=request.generation,
            plan=plan,
        )

    async def _emit_segment(
        self, request: DialogueRequest, segment: UtteranceSegment
    ) -> AsyncIterator[DialogueStreamEvent]:
        self._check_generation(request.generation)
        yield SegmentReady(
            turn_id=request.turn_id,
            generation=request.generation,
            segment=segment,
        )
        async for chunk in self._speech.synthesize(segment):
            self._check_generation(request.generation)
            yield SegmentAudioReady(
                turn_id=request.turn_id,
                generation=request.generation,
                segment=segment,
                chunk=chunk,
            )

    def _check_generation(self, generation: int) -> None:
        if not self._is_generation_current(generation):
            raise DialogueCancelled(f"dialogue generation {generation} is no longer current")

