"""Supervised per-session actor and generation-gated result publication."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine, Hashable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from eeveetuber.domain.events import EventEnvelope
from eeveetuber.domain.interaction import InteractionState, InteractionStateMachine
from eeveetuber.runtime.cancellation import (
    CancellationGeneration,
    CancellationSource,
    CancellationToken,
    StaleGenerationError,
)
from eeveetuber.runtime.mailbox import (
    MailboxClosed,
    OverflowPolicy,
    PriorityMailbox,
    PutOutcome,
    PutResult,
)


class SessionLifecycle(StrEnum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class SessionLifecycleError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SessionMessage:
    event: EventEnvelope
    token: CancellationToken

    @property
    def generation(self) -> CancellationGeneration:
        return self.token.generation

    @property
    def priority(self) -> int:
        return self.event.priority


@dataclass(frozen=True, slots=True)
class SessionSubmission:
    generation: CancellationGeneration
    event: EventEnvelope
    outcome: PutOutcome
    displaced_event_id: UUID | None = None

    @property
    def accepted(self) -> bool:
        return self.outcome is not PutOutcome.REJECTED_FULL


class SessionHandler(Protocol):
    async def __call__(
        self,
        context: SessionActorContext,
        message: SessionMessage,
    ) -> None: ...


class SessionActorContext:
    """Narrow capability passed to the serialized session handler."""

    __slots__ = ("_actor", "_generation")

    def __init__(self, actor: SessionActor, generation: CancellationGeneration) -> None:
        self._actor = actor
        self._generation = generation

    @property
    def session_id(self) -> UUID:
        return self._actor.session_id

    @property
    def generation(self) -> CancellationGeneration:
        return self._generation

    @property
    def interaction_state(self) -> InteractionState:
        return self._actor.interaction_state

    def transition_interaction(
        self,
        requested: InteractionState,
        *,
        reason: str,
    ) -> None:
        self._actor.transition_interaction(requested, reason=reason)

    async def transition_interaction_if_current(
        self,
        requested: InteractionState,
        *,
        reason: str,
    ) -> bool:
        """Allow spawned work to transition state only while its generation is current."""

        return await self._actor.transition_interaction_if_current(
            requested,
            self._generation,
            reason=reason,
        )

    async def publish(
        self,
        event: EventEnvelope,
        *,
        generation: CancellationGeneration | None = None,
    ) -> bool:
        return await self._actor.publish_result(event, generation or self._generation)

    def spawn(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]:
        return self._actor.spawn(coroutine, name=name)


class SessionActor:
    """Own one session's ordering, state, cancellation, and async task lifetime.

    Foreground replacement advances cancellation before the new command is
    queued. Adapter results must call :meth:`publish_result`; direct transport
    writes would bypass the late-result invariant.
    """

    def __init__(
        self,
        handler: SessionHandler,
        *,
        session_id: UUID | None = None,
        inbox_capacity: int = 128,
        outbox_capacity: int = 256,
        inbox_overflow: OverflowPolicy = OverflowPolicy.DROP_LOWEST,
        inbox_coalesce_key: Callable[[SessionMessage], Hashable | None] | None = None,
        inbox_coalescer: Callable[[SessionMessage, SessionMessage], SessionMessage] | None = None,
    ) -> None:
        if inbox_overflow is OverflowPolicy.COALESCE and inbox_coalesce_key is None:
            raise ValueError("a coalescing session inbox requires inbox_coalesce_key")
        self.session_id = session_id or uuid4()
        self._handler = handler
        self._inbox = PriorityMailbox[SessionMessage](
            inbox_capacity,
            overflow=inbox_overflow,
            priority_of=lambda message: message.priority,
            coalesce_key=inbox_coalesce_key,
            coalescer=inbox_coalescer,
        )
        self._outbox = PriorityMailbox[EventEnvelope](
            outbox_capacity,
            overflow=OverflowPolicy.DROP_LOWEST,
            priority_of=lambda event: event.priority,
        )
        self._cancellation = CancellationSource()
        self._interaction = InteractionStateMachine()
        self._lifecycle = SessionLifecycle.NEW
        self._failure: BaseException | None = None
        self._actor_task: asyncio.Task[None] | None = None
        self._children: set[asyncio.Task[Any]] = set()
        self._stopped = asyncio.Event()
        self._lifecycle_lock = asyncio.Lock()
        self._submission_lock = asyncio.Lock()
        self._generation_gate = asyncio.Lock()
        self._sequence_lock = asyncio.Lock()
        self._sequence = 0
        self._stale_results = 0

    @property
    def lifecycle(self) -> SessionLifecycle:
        return self._lifecycle

    @property
    def interaction_state(self) -> InteractionState:
        return self._interaction.state

    @property
    def interaction_revision(self) -> int:
        return self._interaction.revision

    @property
    def current_generation(self) -> CancellationGeneration:
        return self._cancellation.generation

    @property
    def failure(self) -> BaseException | None:
        return self._failure

    @property
    def stale_results(self) -> int:
        return self._stale_results

    @property
    def inbox_size(self) -> int:
        return self._inbox.qsize

    @property
    def outbox_size(self) -> int:
        return self._outbox.qsize

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._lifecycle is not SessionLifecycle.NEW:
                raise SessionLifecycleError(
                    f"session {self.session_id} cannot start from {self._lifecycle.value}"
                )
            self._lifecycle = SessionLifecycle.STARTING
            self._actor_task = asyncio.create_task(
                self._run(), name=f"session:{self.session_id}"
            )
            self._lifecycle = SessionLifecycle.RUNNING

    async def submit(self, event: EventEnvelope) -> SessionSubmission:
        """Submit non-turn work under the currently active generation."""

        async with self._submission_lock:
            self._ensure_running()
            async with self._generation_gate:
                token = self._cancellation.ensure_current(self.current_generation)
                return await self._submit_with_token(event, token)

    async def submit_foreground_turn(
        self,
        event: EventEnvelope,
        *,
        reason: str = "foreground turn replaced",
    ) -> SessionSubmission:
        """Supersede old work, then enqueue the newly accepted turn generation.

        If extreme pressure rejects the new command, the prior generation remains
        cancelled. This fail-closed behavior prevents speech from a turn the user
        explicitly attempted to replace.
        """

        async with self._submission_lock:
            self._ensure_running()
            async with self._generation_gate:
                token = self._cancellation.advance(reason)
                return await self._submit_with_token(event, token)

    async def publish_result(
        self,
        event: EventEnvelope,
        generation: CancellationGeneration,
    ) -> bool:
        """Publish only if ``generation`` is current at the outbox commit point."""

        async with self._generation_gate:
            try:
                self._cancellation.ensure_current(generation)
            except StaleGenerationError:
                self._stale_results += 1
                return False
            stamped = await self._stamp(event)
            result = await self._outbox.put(stamped)
            return result.accepted

    async def receive_output(self) -> EventEnvelope:
        """Receive the next priority/FIFO output for a transport adapter."""

        return await self._outbox.get()

    def transition_interaction(self, requested: InteractionState, *, reason: str) -> None:
        """Transition state from serialized handler code."""

        if self._lifecycle not in {SessionLifecycle.RUNNING, SessionLifecycle.STOPPING}:
            raise SessionLifecycleError("interaction state requires a live session")
        self._interaction.transition(requested, reason=reason)

    async def transition_interaction_if_current(
        self,
        requested: InteractionState,
        generation: CancellationGeneration,
        *,
        reason: str,
    ) -> bool:
        """Generation-gated transition for child tasks running beside the actor loop."""

        async with self._generation_gate:
            try:
                self._cancellation.ensure_current(generation)
            except StaleGenerationError:
                self._stale_results += 1
                return False
            self.transition_interaction(requested, reason=reason)
            return True

    def spawn(
        self,
        coroutine: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]:
        """Create a child task whose failure fails and whose stop cancels the actor."""

        if self._lifecycle is not SessionLifecycle.RUNNING:
            coroutine.close()
            raise SessionLifecycleError("cannot spawn work outside a running session")
        task = asyncio.create_task(coroutine, name=name)
        self._children.add(task)
        task.add_done_callback(self._child_done)
        return task

    async def stop(self, *, graceful: bool = True, timeout: float = 5.0) -> None:
        if timeout <= 0:
            raise ValueError("stop timeout must be positive")
        async with self._lifecycle_lock:
            if self._lifecycle in {SessionLifecycle.STOPPED, SessionLifecycle.FAILED}:
                return
            if self._lifecycle is SessionLifecycle.NEW:
                self._lifecycle = SessionLifecycle.STOPPED
                self._cancellation.close()
                await self._inbox.close(discard=True)
                await self._outbox.close(discard=False)
                self._stopped.set()
                return
            self._lifecycle = SessionLifecycle.STOPPING
            self._cancellation.close("session stopping")
            task = self._actor_task
            await self._inbox.close(discard=not graceful)

        if task is None:
            return
        cancelled_by_stop = not graceful
        if cancelled_by_stop:
            task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.CancelledError:
            if not cancelled_by_stop:
                raise
            await self._finalize_cancelled_before_run()
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self._finalize_cancelled_before_run()

    async def wait_stopped(self) -> SessionLifecycle:
        await self._stopped.wait()
        return self._lifecycle

    async def _submit_with_token(
        self,
        event: EventEnvelope,
        token: CancellationToken,
    ) -> SessionSubmission:
        stamped = await self._stamp(event)
        message = SessionMessage(stamped, token)
        result: PutResult[SessionMessage] = await self._inbox.put(message)
        displaced_id = result.displaced.event.event_id if result.displaced else None
        return SessionSubmission(
            generation=token.generation,
            event=stamped,
            outcome=result.outcome,
            displaced_event_id=displaced_id,
        )

    async def _stamp(self, event: EventEnvelope) -> EventEnvelope:
        scoped = event.for_session(self.session_id)
        async with self._sequence_lock:
            sequence = self._sequence
            self._sequence += 1
        return scoped.with_sequence(sequence)

    def _ensure_running(self) -> None:
        if self._lifecycle is not SessionLifecycle.RUNNING:
            raise SessionLifecycleError(
                f"session {self.session_id} is not running ({self._lifecycle.value})"
            )

    async def _run(self) -> None:
        terminal = SessionLifecycle.STOPPED
        try:
            while True:
                try:
                    message = await self._inbox.get()
                except MailboxClosed:
                    break
                context = SessionActorContext(self, message.generation)
                await self._handler(context, message)
        except asyncio.CancelledError:
            if self._failure is not None:
                terminal = SessionLifecycle.FAILED
        except BaseException as exc:
            self._failure = exc
            terminal = SessionLifecycle.FAILED
        finally:
            self._cancellation.close("session actor stopped")
            await self._inbox.close(discard=True)
            await self._cancel_children()
            await self._outbox.close(discard=False)
            if terminal is SessionLifecycle.FAILED and self._interaction.can_transition(
                InteractionState.DEGRADED
            ):
                self._interaction.transition(
                    InteractionState.DEGRADED, reason="session actor failure"
                )
            elif terminal is SessionLifecycle.STOPPED and self._interaction.state is not InteractionState.IDLE:
                if self._interaction.can_transition(InteractionState.IDLE):
                    self._interaction.transition(InteractionState.IDLE, reason="session stopped")
            self._lifecycle = terminal
            self._stopped.set()

    def _child_done(self, task: asyncio.Task[Any]) -> None:
        self._children.discard(task)
        if task.cancelled():
            return
        exception = task.exception()
        if exception is None or self._lifecycle is not SessionLifecycle.RUNNING:
            return
        self._failure = exception
        if self._actor_task is not None:
            self._actor_task.cancel()

    async def _cancel_children(self) -> None:
        tasks = tuple(self._children)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._children.clear()

    async def _finalize_cancelled_before_run(self) -> None:
        """Close an actor cancelled before its coroutine entered its ``finally`` block."""

        if self._stopped.is_set():
            return
        await self._inbox.close(discard=True)
        await self._cancel_children()
        await self._outbox.close(discard=False)
        self._lifecycle = SessionLifecycle.STOPPED
        self._stopped.set()


class SessionSupervisor:
    """Lifecycle owner for isolated actors, including failure observation."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, SessionActor] = {}
        self._watchers: set[asyncio.Task[None]] = set()
        self._failures: dict[UUID, BaseException] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def active_session_ids(self) -> frozenset[UUID]:
        return frozenset(self._sessions)

    @property
    def failures(self) -> Mapping[UUID, BaseException]:
        return dict(self._failures)

    async def start_session(
        self,
        handler: SessionHandler,
        *,
        session_id: UUID | None = None,
        inbox_capacity: int = 128,
        outbox_capacity: int = 256,
    ) -> SessionActor:
        actor = SessionActor(
            handler,
            session_id=session_id,
            inbox_capacity=inbox_capacity,
            outbox_capacity=outbox_capacity,
        )
        async with self._lock:
            if self._closed:
                raise SessionLifecycleError("session supervisor is closed")
            if actor.session_id in self._sessions:
                raise ValueError(f"session {actor.session_id} already exists")
            self._sessions[actor.session_id] = actor
        try:
            await actor.start()
        except BaseException:
            async with self._lock:
                self._sessions.pop(actor.session_id, None)
            raise
        watcher = asyncio.create_task(
            self._watch(actor), name=f"session-supervisor:{actor.session_id}"
        )
        self._watchers.add(watcher)
        watcher.add_done_callback(self._watchers.discard)
        return actor

    def get(self, session_id: UUID) -> SessionActor | None:
        return self._sessions.get(session_id)

    async def stop_session(
        self,
        session_id: UUID,
        *,
        graceful: bool = True,
        timeout: float = 5.0,
    ) -> bool:
        actor = self._sessions.get(session_id)
        if actor is None:
            return False
        await actor.stop(graceful=graceful, timeout=timeout)
        return True

    async def shutdown(self, *, timeout: float = 5.0) -> None:
        async with self._lock:
            self._closed = True
            actors = tuple(self._sessions.values())
        if actors:
            await asyncio.gather(
                *(actor.stop(timeout=timeout) for actor in actors),
                return_exceptions=False,
            )
        watchers = tuple(self._watchers)
        if watchers:
            await asyncio.gather(*watchers, return_exceptions=True)

    async def _watch(self, actor: SessionActor) -> None:
        lifecycle = await actor.wait_stopped()
        async with self._lock:
            if lifecycle is SessionLifecycle.FAILED and actor.failure is not None:
                self._failures[actor.session_id] = actor.failure
            if self._sessions.get(actor.session_id) is actor:
                self._sessions.pop(actor.session_id, None)
