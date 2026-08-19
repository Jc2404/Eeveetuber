"""Voice-specific session commands kept outside the dialogue orchestration module."""

from __future__ import annotations

from uuid import UUID, uuid4

from eeveetuber.domain.events import (
    EventEnvelope,
    EventPriority,
    JsonValue,
    RetentionClass,
    TrustLabel,
    Visibility,
)
from eeveetuber.domain.interaction import InteractionState
from eeveetuber.media import AsrFinal, AsrPartial, PcmFormat, VadSpeechStarted
from eeveetuber.runtime import (
    PutOutcome,
    SessionActor,
    SessionActorContext,
    SessionMessage,
)


class ForegroundVoiceMixin:
    """Transport-neutral voice callbacks and serialized actor handlers."""

    _voice_capture_active: bool

    @property
    def actor(self) -> SessionActor:  # pragma: no cover - implemented by concrete session
        raise NotImplementedError

    @property
    def session_id(self) -> UUID:  # pragma: no cover - implemented by concrete session
        raise NotImplementedError

    async def voice_capture_started(self, stream_id: UUID, pcm_format: PcmFormat) -> None:
        await self._submit_voice_lifecycle(
            EventEnvelope.create(
                "voice.capture_started",
                {
                    "stream_id": str(stream_id),
                    "sample_rate_hz": pcm_format.sample_rate_hz,
                    "channels": pcm_format.channels,
                    "encoding": pcm_format.encoding.value,
                },
                session_id=self.session_id,
                actor_id="operator_microphone",
                trust=TrustLabel.LOCAL_SENSOR,
                visibility=Visibility.PRIVATE,
                priority=EventPriority.CRITICAL,
            )
        )

    async def voice_capture_stopped(self, stream_id: UUID, *, reason: str) -> None:
        await self._submit_voice_lifecycle(
            EventEnvelope.create(
                "voice.capture_stopped",
                {"stream_id": str(stream_id), "reason": reason},
                session_id=self.session_id,
                actor_id="operator_microphone",
                trust=TrustLabel.LOCAL_SENSOR,
                visibility=Visibility.PRIVATE,
                priority=EventPriority.CRITICAL,
            )
        )

    async def _submit_voice_lifecycle(self, event: EventEnvelope) -> None:
        """Admit capture state even when a foreground replacement changes generation.

        Capture start/stop describes the microphone's current state rather than one
        dialogue turn. A replacement generation may cancel a capacity wait, so retry
        the same owner event under the new generation until it is accepted or the
        session itself closes. Critical priority prevents a successfully queued
        lifecycle event from later being displaced by another session command.
        """

        while True:
            submission = await self.actor.submit_wait(event)
            if submission.accepted:
                return
            if submission.outcome is not PutOutcome.CANCELLED:
                raise RuntimeError("voice lifecycle event was not admitted")

    async def voice_speech_started(
        self,
        event: VadSpeechStarted,
        *,
        barge_in: bool,
    ) -> None:
        envelope = EventEnvelope.create(
            "voice.speech_started",
            {
                "stream_id": str(event.stream_id),
                "utterance_id": str(event.utterance_id),
                "captured_at_monotonic_ns": event.at_monotonic_ns,
                "trigger_sequence": event.trigger_sequence,
                "pre_roll_frame_count": event.pre_roll_frame_count,
                "barge_in": barge_in,
            },
            session_id=self.session_id,
            actor_id="operator_microphone",
            trust=TrustLabel.LOCAL_SENSOR,
            visibility=Visibility.PRIVATE,
            priority=EventPriority.CRITICAL,
        )
        if barge_in:
            await self.actor.submit_foreground_turn_wait(envelope, reason="voice_barge_in")
        else:
            await self.actor.submit_wait(envelope)

    async def voice_transcript_partial(self, event: AsrPartial) -> None:
        await self.actor.submit(
            EventEnvelope.create(
                "voice.transcript_partial",
                {
                    "utterance_id": str(event.utterance_id),
                    "revision": event.revision,
                    "text": event.text,
                    "language": event.language,
                    "confidence": event.confidence,
                },
                session_id=self.session_id,
                actor_id="speech_recognizer",
                trust=TrustLabel.LOCAL_SENSOR,
                visibility=Visibility.PRIVATE,
                retention=RetentionClass.EPHEMERAL_MEDIA,
                priority=EventPriority.LOW,
            )
        )

    async def voice_transcript_final(self, event: AsrFinal) -> None:
        text = event.text.strip()
        if not text:
            await self.actor.submit_wait(
                EventEnvelope.create(
                    "voice.transcript_empty",
                    {"utterance_id": str(event.utterance_id)},
                    session_id=self.session_id,
                    actor_id="speech_recognizer",
                    trust=TrustLabel.LOCAL_SENSOR,
                    visibility=Visibility.PRIVATE,
                    priority=EventPriority.HIGH,
                )
            )
            return
        turn_id = uuid4()
        await self.actor.submit_foreground_turn_wait(
            EventEnvelope.create(
                "turn.requested",
                {
                    "text": text,
                    "turn_id": str(turn_id),
                    "input_modality": "voice",
                    "utterance_id": str(event.utterance_id),
                    "language": event.language,
                    "confidence": event.confidence,
                },
                session_id=self.session_id,
                actor_id="owner_voice",
                trust=TrustLabel.LOCAL_SENSOR,
                visibility=Visibility.PRIVATE,
                retention=RetentionClass.TRANSCRIPT,
                priority=EventPriority.HIGH,
            ),
            reason="voice_transcript_ready",
        )

    async def voice_recognition_failed(
        self,
        utterance_id: UUID,
        *,
        error_type: str,
    ) -> None:
        await self.actor.submit_wait(
            EventEnvelope.create(
                "voice.recognition_failed",
                {"utterance_id": str(utterance_id), "error_type": error_type},
                session_id=self.session_id,
                actor_id="speech_recognizer",
                trust=TrustLabel.SYSTEM,
                visibility=Visibility.PRIVATE,
                priority=EventPriority.HIGH,
            )
        )

    async def _accept_voice_capture_started(
        self,
        context: SessionActorContext,
        message: SessionMessage,
    ) -> None:
        self._voice_capture_active = True
        if context.interaction_state is InteractionState.IDLE:
            context.transition_interaction(
                InteractionState.LISTENING,
                reason="microphone capture started",
            )
        elif context.interaction_state is InteractionState.DEGRADED:
            context.transition_interaction(
                InteractionState.LISTENING,
                reason="microphone capture resumed from degraded state",
            )
        await context.publish(
            self._output_event(
                "voice.capture_started",
                {
                    **self._primitive_payload(message.event),
                    "generation": context.generation.value,
                    "interaction_state": context.interaction_state.value,
                },
                cause=message.event,
                priority=EventPriority.HIGH,
            )
        )

    async def _accept_voice_capture_stopped(
        self,
        context: SessionActorContext,
        message: SessionMessage,
    ) -> None:
        self._voice_capture_active = False
        if context.interaction_state is InteractionState.LISTENING:
            context.transition_interaction(
                InteractionState.IDLE,
                reason="microphone capture stopped",
            )
        await context.publish(
            self._output_event(
                "voice.capture_stopped",
                {
                    **self._primitive_payload(message.event),
                    "generation": context.generation.value,
                    "interaction_state": context.interaction_state.value,
                },
                cause=message.event,
                priority=EventPriority.HIGH,
            )
        )

    async def _accept_voice_speech_started(
        self,
        context: SessionActorContext,
        message: SessionMessage,
    ) -> None:
        barge_in = message.event.payload.get("barge_in") is True
        if barge_in:
            self._enter_listening(context, reason="voice activity interrupted foreground work")
            await context.publish(
                self._output_event(
                    "speech.cancelled",
                    {
                        "generation": context.generation.value,
                        "interaction_state": context.interaction_state.value,
                        "reason": "voice_barge_in",
                    },
                    cause=message.event,
                    priority=EventPriority.CRITICAL,
                )
            )
        elif context.interaction_state in {
            InteractionState.IDLE,
            InteractionState.DEGRADED,
        }:
            self._enter_listening(context, reason="voice activity detected")
        await context.publish(
            self._output_event(
                "voice.speech_started",
                {
                    **self._primitive_payload(message.event),
                    "generation": context.generation.value,
                    "interaction_state": context.interaction_state.value,
                },
                cause=message.event,
                priority=EventPriority.CRITICAL,
            )
        )

    async def _publish_voice_trace(
        self,
        context: SessionActorContext,
        message: SessionMessage,
    ) -> None:
        await context.publish(
            self._output_event(
                message.event.type,
                {
                    **self._primitive_payload(message.event),
                    "generation": context.generation.value,
                },
                cause=message.event,
                priority=EventPriority.LOW,
                retention=message.event.retention,
            )
        )

    async def _accept_voice_recognition_failed(
        self,
        context: SessionActorContext,
        message: SessionMessage,
    ) -> None:
        await context.publish(
            self._output_event(
                "voice.recognition_failed",
                {
                    **self._primitive_payload(message.event),
                    "generation": context.generation.value,
                    "detail": "Speech recognition failed; please try again.",
                    "recoverable": True,
                },
                cause=message.event,
                priority=EventPriority.HIGH,
            )
        )

    async def _accept_cancel(
        self,
        context: SessionActorContext,
        message: SessionMessage,
    ) -> None:
        state = context.interaction_state
        target = (
            InteractionState.LISTENING
            if self._voice_capture_active
            else InteractionState.IDLE
        )
        if state is InteractionState.DEGRADED:
            context.transition_interaction(target, reason="cancel degraded state")
        elif state is InteractionState.IDLE and target is InteractionState.LISTENING:
            context.transition_interaction(target, reason="return to active microphone")
        elif state is InteractionState.LISTENING and target is InteractionState.IDLE:
            context.transition_interaction(target, reason="foreground cancelled")
        elif state not in {InteractionState.IDLE, InteractionState.LISTENING}:
            if state is not InteractionState.INTERRUPTING:
                context.transition_interaction(
                    InteractionState.INTERRUPTING,
                    reason="foreground cancellation accepted",
                )
            context.transition_interaction(target, reason="foreground cancelled")
        await context.publish(
            self._output_event(
                "speech.cancelled",
                {
                    "generation": context.generation.value,
                    "interaction_state": target.value,
                },
                cause=message.event,
                priority=EventPriority.CRITICAL,
            )
        )

    async def _finish_interaction_after_turn(self, context: SessionActorContext) -> None:
        if context.interaction_state is not InteractionState.IDLE:
            transitioned = await context.transition_interaction_if_current(
                InteractionState.IDLE,
                reason="utterance stream completed",
            )
            if not transitioned:
                return
        if self._voice_capture_active:
            await context.transition_interaction_if_current(
                InteractionState.LISTENING,
                reason="microphone remains active after utterance",
            )

    def _enter_listening(self, context: SessionActorContext, *, reason: str) -> None:
        state = context.interaction_state
        if state in {
            InteractionState.PROCESSING,
            InteractionState.SPEAKING,
            InteractionState.WAITING_APPROVAL,
        }:
            context.transition_interaction(InteractionState.INTERRUPTING, reason=reason)
            state = InteractionState.INTERRUPTING
        if state in {
            InteractionState.IDLE,
            InteractionState.INTERRUPTING,
            InteractionState.DEGRADED,
        }:
            context.transition_interaction(InteractionState.LISTENING, reason=reason)

    @staticmethod
    def _primitive_payload(event: EventEnvelope) -> dict[str, JsonValue]:
        payload = event.to_dict()["payload"]
        if not isinstance(payload, dict):  # defensive EventEnvelope invariant
            raise TypeError("session command payload must be an object")
        return payload

    def _output_event(
        self,
        event_type: str,
        payload: dict[str, JsonValue],
        *,
        cause: EventEnvelope | None = None,
        priority: EventPriority = EventPriority.NORMAL,
        retention: RetentionClass = RetentionClass.OPERATIONAL_TRACE,
    ) -> EventEnvelope:  # pragma: no cover - implemented by concrete session
        raise NotImplementedError


__all__ = ["ForegroundVoiceMixin"]
