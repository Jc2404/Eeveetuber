"""Off-path persistence for the ordered session event stream."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Protocol

from eeveetuber.domain.events import EventEnvelope, RetentionClass
from eeveetuber.observability import get_logger
from eeveetuber.storage import EventRecord


class EventRecordSink(Protocol):
    """Small storage boundary implemented by ``EventRepository``."""

    def append(self, record: EventRecord) -> EventRecord: ...


@dataclass(frozen=True, slots=True)
class EventRecorderStats:
    queued: int
    persisted: int
    dropped: int
    failures: int


class AsyncEventRecorder:
    """Persist stamped events sequentially without awaiting SQLite on the dialogue path.

    The recorder is deliberately bounded. Under extreme pressure, ephemeral media and
    operational traces give way to transcript/audit/domain events. Audio bytes are never copied
    into the event journal; their ordering and playback metadata remain replayable.
    """

    _DISPLACEABLE = frozenset(
        {RetentionClass.EPHEMERAL_MEDIA, RetentionClass.OPERATIONAL_TRACE}
    )
    _NEVER_PERSIST = frozenset({"voice.transcript_partial"})

    def __init__(self, sink: EventRecordSink, *, capacity: int = 8_192) -> None:
        if capacity < 1:
            raise ValueError("event recorder capacity must be positive")
        self._sink = sink
        self._capacity = capacity
        self._pending: deque[EventEnvelope] = deque()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._persisted = 0
        self._dropped = 0
        self._failures = 0
        self._logger = get_logger(component="event_recorder")

    @property
    def stats(self) -> EventRecorderStats:
        return EventRecorderStats(
            queued=len(self._pending),
            persisted=self._persisted,
            dropped=self._dropped,
            failures=self._failures,
        )

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("event recorder is already started")
        if self._closed:
            raise RuntimeError("event recorder is closed")
        self._task = asyncio.create_task(self._run(), name="session-event-recorder")

    def observe(self, event: EventEnvelope) -> None:
        """Enqueue one event synchronously; suitable for ``SessionActor`` callbacks."""

        # Interim ASR hypotheses may contain abandoned or misheard private speech.
        # Filter by semantic event type as a defense against an upstream retention
        # regression; partial text must never enter the durable event journal.
        if event.type in self._NEVER_PERSIST:
            return
        if self._closed:
            self._dropped += 1
            return
        if len(self._pending) >= self._capacity and not self._make_room(event):
            self._dropped += 1
            self._logger.warning(
                "event_recording_dropped",
                event_type=event.type,
                event_sequence=event.sequence,
                retention=event.retention.value,
                dropped=self._dropped,
            )
            return
        self._pending.append(event)
        self._wake.set()

    async def close(self) -> EventRecorderStats:
        if self._closed:
            if self._task is not None:
                await asyncio.shield(self._task)
            return self.stats
        self._closed = True
        self._wake.set()
        if self._task is not None:
            await asyncio.shield(self._task)
        return self.stats

    def _make_room(self, incoming: EventEnvelope) -> bool:
        if incoming.retention in self._DISPLACEABLE:
            return False
        for index, queued in enumerate(self._pending):
            if queued.retention in self._DISPLACEABLE:
                del self._pending[index]
                self._dropped += 1
                return True
        return False

    async def _run(self) -> None:
        while True:
            while self._pending:
                event = self._pending.popleft()
                await self._persist(event)
            if self._closed:
                return
            self._wake.clear()
            if self._pending:
                continue
            await self._wake.wait()

    async def _persist(self, event: EventEnvelope) -> None:
        try:
            await asyncio.to_thread(self._sink.append, _event_record(event))
        except Exception as error:
            self._failures += 1
            self._logger.error(
                "event_recording_failed",
                event_type=event.type,
                event_sequence=event.sequence,
                session_id=str(event.session_id) if event.session_id else None,
                error_type=type(error).__name__,
            )
            return
        self._persisted += 1
        payload = event.payload
        self._logger.debug(
            "session_event",
            event_type=event.type,
            event_sequence=event.sequence,
            session_id=str(event.session_id) if event.session_id else None,
            correlation_id=str(event.correlation_id),
            generation=payload.get("generation"),
            turn_id=payload.get("turn_id"),
            segment_sequence=payload.get("sequence"),
            stop_reason=payload.get("stop_reason"),
            segment_count=payload.get("segment_count"),
            input_tokens=payload.get("input_tokens"),
            output_tokens=payload.get("output_tokens"),
        )


def _event_record(event: EventEnvelope) -> EventRecord:
    envelope = event.to_dict()
    payload = envelope.get("payload")
    if event.retention is RetentionClass.EPHEMERAL_MEDIA and isinstance(payload, dict):
        encoded_audio = payload.pop("audio_base64", None)
        if isinstance(encoded_audio, str):
            payload["audio_redacted"] = True
            payload["audio_base64_chars"] = len(encoded_audio)
    return EventRecord(
        event_id=str(event.event_id),
        event_type=event.type,
        payload=envelope,
        created_at=event.occurred_at,
        session_id=str(event.session_id) if event.session_id else None,
        correlation_id=str(event.correlation_id),
        causation_id=str(event.causation_id) if event.causation_id else None,
        actor_id=event.actor_id,
    )


__all__ = ["AsyncEventRecorder", "EventRecorderStats"]
