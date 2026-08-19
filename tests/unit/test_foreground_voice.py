from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from eeveetuber.application.foreground_voice import ForegroundVoiceMixin
from eeveetuber.domain.events import EventEnvelope, EventPriority
from eeveetuber.runtime import SessionActor, SessionActorContext, SessionMessage


class _VoiceHarness(ForegroundVoiceMixin):
    def __init__(self, actor: SessionActor) -> None:
        self._test_actor = actor

    @property
    def actor(self) -> SessionActor:
        return self._test_actor

    @property
    def session_id(self) -> UUID:
        return self._test_actor.session_id


@pytest.mark.asyncio
async def test_capture_stop_retries_when_a_new_foreground_generation_cancels_its_wait() -> None:
    handler_entered = asyncio.Event()
    release_handler = asyncio.Event()
    capture_stop_seen = asyncio.Event()

    async def handler(_context: SessionActorContext, message: SessionMessage) -> None:
        name = message.event.type
        if name == "test.blocking":
            handler_entered.set()
            await release_handler.wait()
        elif name == "voice.capture_stopped":
            capture_stop_seen.set()

    actor = SessionActor(handler, inbox_capacity=1)
    harness = _VoiceHarness(actor)
    await actor.start()
    await actor.submit_wait(EventEnvelope.create("test.blocking"))
    await handler_entered.wait()
    await actor.submit_wait(
        EventEnvelope.create("test.queued", priority=EventPriority.CRITICAL)
    )

    capture_stop = asyncio.create_task(
        harness.voice_capture_stopped(
            UUID("00000000-0000-0000-0000-000000000091"),
            reason="operator_requested",
        )
    )
    await asyncio.sleep(0)
    assert not capture_stop.done()

    foreground = asyncio.create_task(
        actor.submit_foreground_turn_wait(
            EventEnvelope.create("test.foreground", priority=EventPriority.CRITICAL),
            reason="voice transcript ready",
        )
    )
    await asyncio.sleep(0)
    assert actor.current_generation.value == 1
    assert not capture_stop.done()

    release_handler.set()
    await asyncio.wait_for(capture_stop, timeout=0.5)
    assert (await asyncio.wait_for(foreground, timeout=0.5)).accepted
    await asyncio.wait_for(capture_stop_seen.wait(), timeout=0.5)
    await actor.stop()
