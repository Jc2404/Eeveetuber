"""One isolated foreground conversation composed from the runtime backbone."""

from __future__ import annotations

import asyncio
import base64
from datetime import UTC, datetime
from uuid import UUID, uuid4

from eeveetuber.application.context_service import CharacterContextService
from eeveetuber.application.conversation_history import RecentConversationHistoryPolicy
from eeveetuber.application.conversation_storage import ConversationStorageCoordinator
from eeveetuber.application.event_recorder import AsyncEventRecorder
from eeveetuber.dialogue.pipeline import DialogueCancelled, DialoguePipeline
from eeveetuber.dialogue.ports import AsyncCloseable, ModelProvider, SpeechSynthesizer
from eeveetuber.dialogue.types import (
    DialogueRequest,
    SegmentAudioReady,
    SegmentReady,
    UtteranceCompleted,
)
from eeveetuber.domain.events import (
    EventEnvelope,
    EventPriority,
    JsonValue,
    RetentionClass,
    TrustLabel,
    Visibility,
)
from eeveetuber.domain.interaction import InteractionState
from eeveetuber.observability import get_logger
from eeveetuber.runtime import (
    SessionActor,
    SessionActorContext,
    SessionMessage,
    SessionSubmission,
    SessionSupervisor,
)
from eeveetuber.storage import MessageRecord, MessageRole, SessionRecord, SqliteStore
from eeveetuber.storage.ids import new_message_id

_LOGGER = get_logger(component="foreground_session")


class ForegroundSession:
    """Application-level session façade used by transport adapters."""

    def __init__(
        self,
        supervisor: SessionSupervisor,
        context_service: CharacterContextService,
        store: SqliteStore,
        model: ModelProvider,
        speech: SpeechSynthesizer,
        *,
        inbox_capacity: int = 128,
        outbox_capacity: int = 256,
        event_recorder_capacity: int = 8_192,
        history_policy: RecentConversationHistoryPolicy | None = None,
    ) -> None:
        self._supervisor = supervisor
        self._context_service = context_service
        self._store = store
        self._model = model
        self._speech = speech
        self._inbox_capacity = inbox_capacity
        self._outbox_capacity = outbox_capacity
        self._conversation_storage = ConversationStorageCoordinator(
            store,
            context_service,
            history_policy or RecentConversationHistoryPolicy(),
        )
        self._event_recorder = AsyncEventRecorder(
            self._store.events,
            capacity=event_recorder_capacity,
        )
        self._actor: SessionActor | None = None
        self._message_sequence = 0

    @property
    def actor(self) -> SessionActor:
        if self._actor is None:
            raise RuntimeError("foreground session has not started")
        return self._actor

    @property
    def session_id(self) -> UUID:
        return self.actor.session_id

    async def start(self) -> None:
        if self._actor is not None:
            raise RuntimeError("foreground session is already started")
        actor = await self._supervisor.start_session(
            self._handle_message,
            inbox_capacity=self._inbox_capacity,
            outbox_capacity=self._outbox_capacity,
            event_observer=self._event_recorder.observe,
        )
        self._actor = actor
        try:
            await asyncio.to_thread(
                self._store.sessions.create,
                SessionRecord(
                    session_id=str(actor.session_id),
                    namespace=self._context_service.namespace,
                    created_at=datetime.now(UTC),
                    metadata={"transport": "websocket", "schema_version": 1},
                ),
            )
            self._event_recorder.start()
            await actor.publish_result(
                self._output_event(
                    "session.ready",
                    {"generation": 0, "interaction_state": InteractionState.IDLE.value},
                ),
                actor.current_generation,
            )
        except BaseException:
            await self._supervisor.stop_session(actor.session_id, graceful=False)
            await self._event_recorder.close()
            self._actor = None
            raise

    async def submit_text(self, text: str, *, actor_id: str = "owner") -> SessionSubmission:
        stripped = text.strip()
        if not stripped:
            raise ValueError("text turn cannot be blank")
        turn_id = uuid4()
        event = EventEnvelope.create(
            "turn.requested",
            {"text": stripped, "turn_id": str(turn_id)},
            session_id=self.session_id,
            actor_id=actor_id,
            trust=TrustLabel.OWNER,
            visibility=Visibility.PRIVATE,
            retention=RetentionClass.TRANSCRIPT,
            priority=EventPriority.HIGH,
        )
        return await self.actor.submit_foreground_turn(event)

    async def cancel(self, *, reason: str = "user_requested") -> SessionSubmission:
        event = EventEnvelope.create(
            "turn.cancel_requested",
            {"reason": reason},
            session_id=self.session_id,
            actor_id="owner",
            trust=TrustLabel.OWNER,
            visibility=Visibility.PRIVATE,
            priority=EventPriority.CRITICAL,
        )
        return await self.actor.submit_foreground_turn(event, reason=reason)

    async def ping(self, causation_id: UUID) -> SessionSubmission:
        return await self.actor.submit(
            EventEnvelope.create(
                "transport.ping",
                session_id=self.session_id,
                causation_id=causation_id,
                priority=EventPriority.LOW,
            )
        )

    async def request_neutral_avatar(self, causation_id: UUID) -> SessionSubmission:
        return await self.actor.submit(
            EventEnvelope.create(
                "operator.neutral_avatar_requested",
                session_id=self.session_id,
                actor_id="owner",
                causation_id=causation_id,
                trust=TrustLabel.OWNER,
                priority=EventPriority.CRITICAL,
            )
        )

    async def acknowledge_playback(
        self,
        *,
        causation_id: UUID,
        audio_event_id: UUID,
        generation: int,
        event_sequence: int,
        segment_id: UUID,
        chunk_index: int,
        state: str,
        client_monotonic_ms: int,
        played_ms: int | None,
        detail: str | None,
    ) -> SessionSubmission:
        return await self.actor.submit(
            EventEnvelope.create(
                "playback.ack_received",
                {
                    "audio_event_id": str(audio_event_id),
                    "generation": generation,
                    "event_sequence": event_sequence,
                    "segment_id": str(segment_id),
                    "chunk_index": chunk_index,
                    "state": state,
                    "client_monotonic_ms": client_monotonic_ms,
                    "played_ms": played_ms,
                    "detail": detail,
                },
                session_id=self.session_id,
                actor_id="operator",
                causation_id=causation_id,
                trust=TrustLabel.OWNER,
                visibility=Visibility.PRIVATE,
                retention=RetentionClass.OPERATIONAL_TRACE,
                priority=EventPriority.LOW,
            )
        )

    async def receive_output(self) -> EventEnvelope:
        return await self.actor.receive_output()

    async def stop(self) -> None:
        if self._actor is not None:
            await self._supervisor.stop_session(self._actor.session_id, graceful=False)
        await self._conversation_storage.drain()
        recorder_stats = await self._event_recorder.close()
        if recorder_stats.dropped or recorder_stats.failures:
            _LOGGER.warning(
                "event_recorder_closed_with_loss",
                session_id=str(self._actor.session_id) if self._actor else None,
                persisted=recorder_stats.persisted,
                dropped=recorder_stats.dropped,
                failures=recorder_stats.failures,
            )
        else:
            _LOGGER.debug(
                "event_recorder_closed",
                session_id=str(self._actor.session_id) if self._actor else None,
                persisted=recorder_stats.persisted,
            )
        closed: set[int] = set()
        for adapter in (self._model, self._speech):
            if isinstance(adapter, AsyncCloseable) and id(adapter) not in closed:
                await adapter.aclose()
                closed.add(id(adapter))

    async def _handle_message(
        self,
        context: SessionActorContext,
        message: SessionMessage,
    ) -> None:
        match message.event.type:
            case "turn.requested":
                await self._accept_turn(context, message)
            case "turn.cancel_requested":
                await self._accept_cancel(context, message)
            case "transport.ping":
                await context.publish(
                    self._output_event(
                        "transport.pong",
                        {"generation": context.generation.value},
                        cause=message.event,
                    )
                )
            case "operator.neutral_avatar_requested":
                await context.publish(
                    self._output_event(
                        "avatar.neutral_requested",
                        {"generation": context.generation.value},
                        cause=message.event,
                        priority=EventPriority.CRITICAL,
                    )
                )
            case "playback.ack_received":
                acknowledged_generation = message.event.payload.get("generation")
                primitive_payload = message.event.to_dict()["payload"]
                if not isinstance(primitive_payload, dict):  # defensive event invariant
                    raise TypeError("playback acknowledgement payload must be an object")
                await context.publish(
                    self._output_event(
                        "playback.acknowledged",
                        {
                            **primitive_payload,
                            "accepted": acknowledged_generation == context.generation.value,
                            "current_generation": context.generation.value,
                        },
                        cause=message.event,
                        priority=EventPriority.LOW,
                        retention=RetentionClass.OPERATIONAL_TRACE,
                    )
                )
            case _:  # pragma: no cover - actor receives only application-created commands
                raise ValueError(f"unsupported session command {message.event.type!r}")

    async def _accept_turn(
        self,
        context: SessionActorContext,
        message: SessionMessage,
    ) -> None:
        self._enter_processing(context)
        raw_text = message.event.payload.get("text")
        raw_turn_id = message.event.payload.get("turn_id")
        if not isinstance(raw_text, str) or not isinstance(raw_turn_id, str):
            raise TypeError("turn.requested payload is invalid")
        turn_id = UUID(raw_turn_id)
        user_sequence = self._next_message_sequence()
        assistant_sequence = self._next_message_sequence()
        self._conversation_storage.persist_message(
            context,
            MessageRecord(
                message_id=new_message_id(),
                session_id=str(context.session_id),
                sequence=user_sequence,
                role=MessageRole.USER,
                content=raw_text,
                created_at=message.event.occurred_at,
                actor_id=message.event.actor_id,
                source_event_id=str(message.event.event_id),
                metadata={"turn_id": str(turn_id), "generation": context.generation.value},
            ),
            name=f"persist-user:{turn_id}",
        )
        await context.publish(
            self._output_event(
                "turn.accepted",
                {
                    "turn_id": str(turn_id),
                    "generation": context.generation.value,
                    "interaction_state": InteractionState.PROCESSING.value,
                },
                cause=message.event,
            )
        )
        context.spawn(
            self._run_turn(
                context,
                message,
                turn_id,
                raw_text,
                user_sequence,
                assistant_sequence,
            ),
            name=f"dialogue:{turn_id}",
        )

    async def _accept_cancel(
        self,
        context: SessionActorContext,
        message: SessionMessage,
    ) -> None:
        state = context.interaction_state
        if state is InteractionState.DEGRADED:
            context.transition_interaction(InteractionState.IDLE, reason="cancel degraded state")
        elif state is not InteractionState.IDLE:
            if state is not InteractionState.INTERRUPTING:
                context.transition_interaction(
                    InteractionState.INTERRUPTING,
                    reason="foreground cancellation accepted",
                )
            context.transition_interaction(InteractionState.IDLE, reason="foreground cancelled")
        await context.publish(
            self._output_event(
                "speech.cancelled",
                {
                    "generation": context.generation.value,
                    "interaction_state": InteractionState.IDLE.value,
                },
                cause=message.event,
                priority=EventPriority.CRITICAL,
            )
        )

    async def _run_turn(
        self,
        context: SessionActorContext,
        message: SessionMessage,
        turn_id: UUID,
        user_text: str,
        user_sequence: int,
        assistant_sequence: int,
    ) -> None:
        try:
            history = await self._conversation_storage.load_recent_history(
                session_id=str(context.session_id),
                before_sequence=user_sequence,
                before_generation=context.generation.value,
            )
            snapshot = self._context_service.compile_for_turn(context.session_id, turn_id)
            self._conversation_storage.persist_snapshot(
                context,
                snapshot,
                name=f"persist-context:{turn_id}",
            )
            await context.publish(
                self._output_event(
                    "context.snapshot_published",
                    {
                        "turn_id": str(turn_id),
                        "generation": context.generation.value,
                        "snapshot_id": snapshot.snapshot_id,
                        "memory_generation": snapshot.revision.memory_generation,
                        "estimated_tokens": snapshot.usage.total_tokens,
                    },
                    cause=message.event,
                )
            )
            pipeline = DialoguePipeline(
                self._model,
                self._speech,
                is_generation_current=lambda generation: (
                    generation == context.generation.value and not message.token.cancelled
                ),
            )
            first_segment = True
            system_context = snapshot.rendered_context
            if history.rendered_context:
                system_context = f"{system_context}\n{history.rendered_context}"
            async for output in pipeline.run(
                request=DialogueRequest(
                    context.session_id,
                    turn_id,
                    context.generation.value,
                    user_text,
                    system_context,
                    metadata={
                        "context_snapshot_id": snapshot.snapshot_id,
                        "history_message_count": str(len(history.messages)),
                        "history_character_count": str(history.character_count),
                    },
                ),
                cancellation=message.token,
            ):
                if isinstance(output, SegmentReady):
                    if first_segment:
                        transitioned = await context.transition_interaction_if_current(
                            InteractionState.SPEAKING,
                            reason="first validated utterance segment",
                        )
                        if not transitioned:
                            return
                        first_segment = False
                    await context.publish(
                        self._output_event(
                            "utterance.segment_ready",
                            {
                                "turn_id": str(turn_id),
                                "generation": context.generation.value,
                                "segment_id": str(output.segment.segment_id),
                                "sequence": output.segment.sequence,
                                "speakable_text": output.segment.speakable_text,
                                "display_text": output.segment.display_text,
                            },
                            cause=message.event,
                        )
                    )
                elif isinstance(output, SegmentAudioReady):
                    await context.publish(
                        self._output_event(
                            "speech.audio_chunk",
                            {
                                "turn_id": str(turn_id),
                                "generation": context.generation.value,
                                "segment_id": str(output.segment.segment_id),
                                "sequence": output.chunk.sequence,
                                "chunk_index": output.chunk.chunk_index,
                                "media_type": output.chunk.media_type,
                                "sample_rate_hz": output.chunk.sample_rate_hz,
                                "is_final": output.chunk.is_final,
                                "duration_ms": output.chunk.duration_ms,
                                "audio_base64": base64.b64encode(output.chunk.audio).decode("ascii"),
                            },
                            cause=message.event,
                            retention=RetentionClass.EPHEMERAL_MEDIA,
                        )
                    )
                elif isinstance(output, UtteranceCompleted):
                    if not output.plan.segments:
                        detail = _empty_output_detail(output.plan.stop_reason.value)
                        published = await context.publish(
                            self._output_event(
                                "turn.failed",
                                {
                                    "turn_id": str(turn_id),
                                    "generation": context.generation.value,
                                    "error_type": "ModelEmptyOutput",
                                    "detail": detail,
                                    "recoverable": True,
                                    "stop_reason": output.plan.stop_reason.value,
                                    "input_tokens": output.plan.input_tokens,
                                    "output_tokens": output.plan.output_tokens,
                                },
                                cause=message.event,
                                priority=EventPriority.HIGH,
                            )
                        )
                        _LOGGER.warning(
                            "model_empty_output",
                            session_id=str(context.session_id),
                            turn_id=str(turn_id),
                            generation=context.generation.value,
                            stop_reason=output.plan.stop_reason.value,
                            input_tokens=output.plan.input_tokens,
                            output_tokens=output.plan.output_tokens,
                        )
                        if published:
                            await context.transition_interaction_if_current(
                                InteractionState.DEGRADED,
                                reason="model completed without visible output",
                            )
                        return
                    async with self._conversation_storage.registration_lock:
                        published = await context.publish(
                            self._output_event(
                                "utterance.completed",
                                {
                                    "turn_id": str(turn_id),
                                    "generation": context.generation.value,
                                    "speakable_text": output.plan.speakable_text,
                                    "display_text": output.plan.display_text,
                                    "segment_count": len(output.plan.segments),
                                    "stop_reason": output.plan.stop_reason.value,
                                    "input_tokens": output.plan.input_tokens,
                                    "output_tokens": output.plan.output_tokens,
                                },
                                cause=message.event,
                                retention=RetentionClass.TRANSCRIPT,
                            )
                        )
                        assistant_text = output.plan.display_text.strip()
                        if published and assistant_text:
                            self._conversation_storage.persist_message(
                                context,
                                MessageRecord(
                                    message_id=new_message_id(),
                                    session_id=str(context.session_id),
                                    sequence=assistant_sequence,
                                    role=MessageRole.ASSISTANT,
                                    content=assistant_text,
                                    created_at=datetime.now(UTC),
                                    source_event_id=str(message.event.event_id),
                                    metadata={
                                        "turn_id": str(turn_id),
                                        "generation": context.generation.value,
                                    },
                                ),
                                name=f"persist-assistant:{turn_id}",
                            )
                    _LOGGER.debug(
                        "model_turn_completed",
                        session_id=str(context.session_id),
                        turn_id=str(turn_id),
                        generation=context.generation.value,
                        stop_reason=output.plan.stop_reason.value,
                        input_tokens=output.plan.input_tokens,
                        output_tokens=output.plan.output_tokens,
                        segment_count=len(output.plan.segments),
                    )
            await context.transition_interaction_if_current(
                InteractionState.IDLE,
                reason="utterance stream completed",
            )
        except DialogueCancelled:
            return
        except asyncio.CancelledError:
            raise
        except Exception as error:
            published = await context.publish(
                self._output_event(
                    "turn.failed",
                    {
                        "turn_id": str(turn_id),
                        "generation": context.generation.value,
                        "error_type": type(error).__name__,
                        "detail": "The turn failed before a reply could be produced.",
                        "recoverable": True,
                    },
                    cause=message.event,
                    priority=EventPriority.HIGH,
                )
            )
            if published:
                await context.transition_interaction_if_current(
                    InteractionState.DEGRADED,
                    reason="foreground dialogue failed",
                )

    def _enter_processing(self, context: SessionActorContext) -> None:
        state = context.interaction_state
        if state is InteractionState.PROCESSING:
            context.transition_interaction(
                InteractionState.INTERRUPTING,
                reason="replacement turn accepted",
            )
            state = InteractionState.INTERRUPTING
        elif state is InteractionState.SPEAKING:
            context.transition_interaction(
                InteractionState.INTERRUPTING,
                reason="barge-in turn accepted",
            )
            state = InteractionState.INTERRUPTING
        elif state is InteractionState.WAITING_APPROVAL:
            context.transition_interaction(
                InteractionState.INTERRUPTING,
                reason="owner replaced approval wait",
            )
            state = InteractionState.INTERRUPTING
        if state is InteractionState.IDLE or state is InteractionState.LISTENING:
            context.transition_interaction(InteractionState.PROCESSING, reason="turn accepted")
        elif state is InteractionState.INTERRUPTING:
            context.transition_interaction(
                InteractionState.PROCESSING,
                reason="replacement turn processing",
            )
        elif state is InteractionState.DEGRADED:
            context.transition_interaction(InteractionState.IDLE, reason="retry from degraded")
            context.transition_interaction(InteractionState.PROCESSING, reason="retry accepted")

    def _next_message_sequence(self) -> int:
        self._message_sequence += 1
        return self._message_sequence

    def _output_event(
        self,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        cause: EventEnvelope | None = None,
        priority: EventPriority = EventPriority.NORMAL,
        retention: RetentionClass = RetentionClass.OPERATIONAL_TRACE,
    ) -> EventEnvelope:
        return EventEnvelope.create(
            event_type,
            payload=payload,
            session_id=self._actor.session_id if self._actor is not None else None,
            correlation_id=cause.correlation_id if cause else None,
            causation_id=cause.event_id if cause else None,
            actor_id="eeveetuber",
            trust=TrustLabel.SYSTEM,
            visibility=Visibility.PRIVATE,
            priority=priority,
            retention=retention,
        )


def _empty_output_detail(stop_reason: str) -> str:
    if stop_reason == "length":
        return (
            "The model reached its output limit before producing visible text. "
            "For realtime chat, use reasoning_effort=none or increase the output limit."
        )
    return "The model returned no visible text. Please try the turn again."
