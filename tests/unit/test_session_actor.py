from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import uuid4

import pytest

from eeveetuber.domain.events import EventEnvelope, EventPriority
from eeveetuber.domain.interaction import InteractionState
from eeveetuber.runtime.cancellation import CancellationGeneration
from eeveetuber.runtime.mailbox import MailboxClosed, PutOutcome
from eeveetuber.runtime.session import (
    SessionActor,
    SessionActorContext,
    SessionLifecycle,
    SessionLifecycleError,
    SessionMessage,
    SessionSupervisor,
)

Handler = Callable[[SessionActorContext, SessionMessage], object]


@pytest.mark.asyncio
async def test_actor_serializes_inputs_stamps_events_and_publishes_results() -> None:
    seen: list[SessionMessage] = []

    async def handler(context: SessionActorContext, message: SessionMessage) -> None:
        seen.append(message)
        await context.publish(
            EventEnvelope.create(
                "agent.completed",
                {"input_event_id": str(message.event.event_id)},
            )
        )

    actor = SessionActor(handler)
    await actor.start()
    submission = await actor.submit(EventEnvelope.create("transcript.final", {"text": "hi"}))
    output = await actor.receive_output()

    assert submission.accepted
    assert submission.generation == CancellationGeneration(0)
    assert submission.event.session_id == actor.session_id
    assert submission.event.sequence == 0
    assert output.session_id == actor.session_id
    assert output.sequence == 1
    assert output.payload["input_event_id"] == str(submission.event.event_id)
    assert [message.event.event_id for message in seen] == [submission.event.event_id]

    await actor.stop()
    assert actor.lifecycle is SessionLifecycle.STOPPED
    with pytest.raises(MailboxClosed):
        await actor.receive_output()


@pytest.mark.asyncio
async def test_actor_observes_only_accepted_stamped_events_in_sequence() -> None:
    observed: list[EventEnvelope] = []

    async def handler(context: SessionActorContext, message: SessionMessage) -> None:
        await context.publish(
            EventEnvelope.create(
                "test.output",
                causation_id=message.event.event_id,
                correlation_id=message.event.correlation_id,
            )
        )

    actor = SessionActor(handler, event_observer=observed.append)
    await actor.start()
    await actor.submit(EventEnvelope.create("test.input"))
    await actor.receive_output()
    await actor.stop()

    assert [event.type for event in observed] == ["test.input", "test.output"]
    assert [event.sequence for event in observed] == [0, 1]
    assert all(event.session_id == actor.session_id for event in observed)


@pytest.mark.asyncio
async def test_replacement_turn_rejects_late_child_result() -> None:
    first_started = asyncio.Event()
    release_late_result = asyncio.Event()
    attempted = asyncio.Event()
    publication_results: list[bool] = []

    async def handler(context: SessionActorContext, message: SessionMessage) -> None:
        if message.event.payload["name"] != "first":
            return

        async def finish_late() -> None:
            first_started.set()
            await release_late_result.wait()
            publication_results.append(
                await context.publish(EventEnvelope.create("speech.completed", {"late": True}))
            )
            attempted.set()

        context.spawn(finish_late(), name="late-provider")

    actor = SessionActor(handler)
    await actor.start()
    first = await actor.submit_foreground_turn(
        EventEnvelope.create("transcript.final", {"name": "first"})
    )
    await first_started.wait()
    second = await actor.submit_foreground_turn(
        EventEnvelope.create("transcript.final", {"name": "second"})
    )
    release_late_result.set()
    await attempted.wait()

    assert first.generation == CancellationGeneration(1)
    assert second.generation == CancellationGeneration(2)
    assert publication_results == [False]
    assert actor.stale_results == 1
    assert actor.outbox_size == 0
    await actor.stop()


@pytest.mark.asyncio
async def test_session_inbox_pressure_evicts_newest_low_priority_item() -> None:
    handler_entered = asyncio.Event()
    release_handler = asyncio.Event()
    two_seen = asyncio.Event()
    seen: list[str] = []

    async def handler(_context: SessionActorContext, message: SessionMessage) -> None:
        seen.append(str(message.event.payload["name"]))
        if len(seen) == 2:
            two_seen.set()
        if message.event.payload["name"] == "blocking":
            handler_entered.set()
            await release_handler.wait()

    actor = SessionActor(handler, inbox_capacity=1)
    await actor.start()
    await actor.submit(EventEnvelope.create("test.command", {"name": "blocking"}))
    await handler_entered.wait()
    low = await actor.submit(
        EventEnvelope.create(
            "test.command", {"name": "low"}, priority=EventPriority.LOW
        )
    )
    high = await actor.submit(
        EventEnvelope.create(
            "test.command", {"name": "high"}, priority=EventPriority.HIGH
        )
    )

    assert low.outcome is PutOutcome.ACCEPTED
    assert high.outcome is PutOutcome.EVICTED_LOWEST
    assert high.displaced_event_id == low.event.event_id
    release_handler.set()
    await two_seen.wait()
    assert seen == ["blocking", "high"]
    await actor.stop()


@pytest.mark.asyncio
async def test_handler_owns_validated_interaction_transitions() -> None:
    complete = asyncio.Event()

    async def handler(context: SessionActorContext, _message: SessionMessage) -> None:
        context.transition_interaction(InteractionState.PROCESSING, reason="final input")
        context.transition_interaction(InteractionState.SPEAKING, reason="first audio")
        context.transition_interaction(InteractionState.INTERRUPTING, reason="barge-in")
        context.transition_interaction(InteractionState.LISTENING, reason="listen again")
        complete.set()

    actor = SessionActor(handler)
    await actor.start()
    await actor.submit(EventEnvelope.create("test.command"))
    await complete.wait()

    assert actor.interaction_state is InteractionState.LISTENING
    assert actor.interaction_revision == 4
    await actor.stop()
    assert actor.interaction_state is InteractionState.IDLE


@pytest.mark.asyncio
async def test_child_failure_fails_actor_and_supervisor_records_it() -> None:
    async def handler(context: SessionActorContext, _message: SessionMessage) -> None:
        async def fail() -> None:
            await asyncio.sleep(0)
            raise LookupError("adapter exploded")

        context.spawn(fail(), name="failing-adapter")

    supervisor = SessionSupervisor()
    actor = await supervisor.start_session(handler)
    await actor.submit(EventEnvelope.create("test.command"))

    assert await actor.wait_stopped() is SessionLifecycle.FAILED
    await asyncio.sleep(0)
    assert isinstance(actor.failure, LookupError)
    assert actor.interaction_state is InteractionState.DEGRADED
    assert isinstance(supervisor.failures[actor.session_id], LookupError)
    assert actor.session_id not in supervisor.active_session_ids
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_supervisor_keeps_sessions_isolated_and_shutdown_is_idempotent() -> None:
    async def handler(context: SessionActorContext, message: SessionMessage) -> None:
        await context.publish(
            EventEnvelope.create(
                "agent.completed",
                {"source_session": str(context.session_id), "value": message.event.payload["value"]},
            )
        )

    supervisor = SessionSupervisor()
    first = await supervisor.start_session(handler)
    second = await supervisor.start_session(handler)
    await first.submit(EventEnvelope.create("test.command", {"value": "first"}))
    await second.submit(EventEnvelope.create("test.command", {"value": "second"}))

    first_output, second_output = await asyncio.gather(
        first.receive_output(), second.receive_output()
    )
    assert first_output.payload == {
        "source_session": str(first.session_id),
        "value": "first",
    }
    assert second_output.payload == {
        "source_session": str(second.session_id),
        "value": "second",
    }
    assert first_output.session_id != second_output.session_id

    await supervisor.shutdown()
    await supervisor.shutdown()
    assert not supervisor.active_session_ids


@pytest.mark.asyncio
async def test_lifecycle_and_session_scope_are_enforced() -> None:
    async def handler(_context: SessionActorContext, _message: SessionMessage) -> None:
        return

    actor = SessionActor(handler)
    with pytest.raises(SessionLifecycleError, match="not running"):
        await actor.submit(EventEnvelope.create("test.command"))
    await actor.start()
    with pytest.raises(SessionLifecycleError, match="cannot start"):
        await actor.start()
    with pytest.raises(ValueError, match="event belongs to session"):
        await actor.submit(
            EventEnvelope.create("test.command", session_id=uuid4())
        )
    await actor.stop(graceful=False)
    assert actor.lifecycle is SessionLifecycle.STOPPED


@pytest.mark.asyncio
async def test_spawned_context_cannot_transition_after_generation_advance() -> None:
    async def handler(_context: SessionActorContext, _message: SessionMessage) -> None:
        return

    actor = SessionActor(handler)
    await actor.start()
    old_context = SessionActorContext(actor, actor.current_generation)

    await actor.submit_foreground_turn(EventEnvelope.create("turn.new"))

    assert not await old_context.transition_interaction_if_current(
        InteractionState.SPEAKING,
        reason="late child result",
    )
    assert actor.interaction_state is InteractionState.IDLE
    await actor.stop()
    assert actor.lifecycle is SessionLifecycle.STOPPED
