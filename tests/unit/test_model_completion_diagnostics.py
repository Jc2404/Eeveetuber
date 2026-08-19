from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from eeveetuber.adapters.fake import FakeSpeechSynthesizer
from eeveetuber.dialogue.pipeline import DialoguePipeline
from eeveetuber.dialogue.types import (
    DialogueRequest,
    ModelCompleted,
    ModelStopReason,
    ModelStreamEvent,
    UtteranceCompleted,
    UtterancePlan,
)
from eeveetuber.runtime.cancellation import CancellationToken


class _EmptyDiagnosticModel:
    async def stream(
        self,
        _request: DialogueRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        yield ModelCompleted(
            stop_reason=ModelStopReason.LENGTH,
            input_tokens=27,
            output_tokens=0,
        )


@pytest.mark.asyncio
async def test_pipeline_exposes_completion_diagnostics_for_zero_visible_output() -> None:
    pipeline = DialoguePipeline(
        _EmptyDiagnosticModel(),
        FakeSpeechSynthesizer(),
        is_generation_current=lambda generation: generation == 4,
    )
    request = DialogueRequest(
        session_id=uuid4(),
        turn_id=uuid4(),
        generation=4,
        user_text="respond",
        system_context="persona",
    )

    events = [event async for event in pipeline.run(request)]

    assert len(events) == 1
    completed = events[0]
    assert isinstance(completed, UtteranceCompleted)
    assert completed.plan.display_text == ""
    assert completed.plan.speakable_text == ""
    assert completed.plan.stop_reason is ModelStopReason.LENGTH
    assert completed.plan.input_tokens == 27
    assert completed.plan.output_tokens == 0


@pytest.mark.parametrize("invalid", [-1, True])
def test_completion_domain_rejects_invalid_token_diagnostics(invalid: int) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        ModelCompleted(input_tokens=invalid)
    with pytest.raises(ValueError, match="non-negative integer"):
        UtterancePlan(
            turn_id=uuid4(),
            generation=0,
            segments=(),
            stop_reason=ModelStopReason.COMPLETE,
            output_tokens=invalid,
        )
