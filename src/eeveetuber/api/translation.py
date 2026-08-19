"""Translate immutable domain events into transport DTOs."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from uuid import UUID

from eeveetuber.api.audio_frames import AudioFrame, AudioFrameFlags
from eeveetuber.api.protocol import (
    ServerMessage,
    StatusCode,
    StatusMessage,
    StatusScope,
)
from eeveetuber.domain.events import EventEnvelope

_STATUS_PROJECTIONS: dict[str, tuple[StatusScope, StatusCode]] = {
    "session.ready": (StatusScope.SESSION, StatusCode.READY),
    "turn.accepted": (StatusScope.TURN, StatusCode.PROCESSING),
    "utterance.segment_ready": (StatusScope.TURN, StatusCode.SPEAKING),
    "speech.cancelled": (StatusScope.AUDIO, StatusCode.READY),
    "utterance.completed": (StatusScope.TURN, StatusCode.READY),
    "turn.failed": (StatusScope.TURN, StatusCode.ERROR),
    "voice.capture_started": (StatusScope.SESSION, StatusCode.LISTENING),
    "voice.capture_stopped": (StatusScope.SESSION, StatusCode.READY),
    "voice.speech_started": (StatusScope.TURN, StatusCode.LISTENING),
    "voice.transcript_final": (StatusScope.TURN, StatusCode.PROCESSING),
    "voice.recognition_failed": (StatusScope.TURN, StatusCode.ERROR),
}


def event_to_server_message(event: EventEnvelope, *, generation: int) -> ServerMessage:
    if event.session_id is None:
        raise ValueError("transport output event must be scoped to a session")
    if event.sequence is None:
        raise ValueError("transport output event must have an owner-assigned sequence")
    primitive = event.to_dict()
    payload = primitive["payload"]
    if not isinstance(payload, dict):  # defensive: EventEnvelope guarantees a mapping
        raise TypeError("event payload must translate to an object")
    return ServerMessage(
        message_id=event.event_id,
        type=event.type,
        occurred_at=event.occurred_at,
        session_id=event.session_id,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        sequence=event.sequence,
        generation=generation,
        data=payload,
    )


def event_to_status_message(event: EventEnvelope, *, generation: int) -> StatusMessage | None:
    """Project lifecycle events into the stable operator status contract."""

    projection = _STATUS_PROJECTIONS.get(event.type)
    if projection is None:
        return None
    session_id, sequence = _transport_identity(event)
    scope, status = projection
    return StatusMessage(
        session_id=session_id,
        correlation_id=event.correlation_id,
        causation_id=event.event_id,
        sequence=sequence,
        generation=generation,
        scope=scope,
        status=status,
        detail="turn failed" if status is StatusCode.ERROR else None,
        recoverable=status is not StatusCode.ERROR,
    )


def event_to_audio_frame(event: EventEnvelope, *, generation: int) -> AudioFrame:
    """Translate one synthesized-audio event into the EVAF v1 binary contract."""

    if event.type != "speech.audio_chunk":
        raise ValueError("only speech.audio_chunk events can become audio frames")
    session_id, event_sequence = _transport_identity(event)
    payload = event.payload
    raw_audio = _string(payload, "audio_base64")
    try:
        audio = base64.b64decode(raw_audio, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError("speech audio payload is not valid base64") from error
    flags = AudioFrameFlags.NONE
    if _boolean(payload, "is_final"):
        flags |= AudioFrameFlags.FINAL_CHUNK
    if payload.get("end_utterance") is True:
        flags |= AudioFrameFlags.END_UTTERANCE
    return AudioFrame(
        session_id=session_id,
        event_id=event.event_id,
        correlation_id=event.correlation_id,
        turn_id=UUID(_string(payload, "turn_id")),
        segment_id=UUID(_string(payload, "segment_id")),
        generation=generation,
        event_sequence=event_sequence,
        segment_sequence=_integer(payload, "sequence"),
        chunk_index=_integer(payload, "chunk_index"),
        sample_rate_hz=_integer(payload, "sample_rate_hz"),
        channels=_integer(payload, "channels", default=1),
        media_type=_string(payload, "media_type"),
        audio=audio,
        duration_ms=_optional_integer(payload, "duration_ms"),
        flags=flags,
    )


def _transport_identity(event: EventEnvelope) -> tuple[UUID, int]:
    if event.session_id is None:
        raise ValueError("transport output event must be scoped to a session")
    if event.sequence is None:
        raise ValueError("transport output event must have an owner-assigned sequence")
    return event.session_id, event.sequence


def _string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"audio payload field {key!r} must be non-blank text")
    return value


def _integer(payload: Mapping[str, object], key: str, *, default: int | None = None) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"audio payload field {key!r} must be an integer")
    return value


def _optional_integer(payload: Mapping[str, object], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"audio payload field {key!r} must be an integer or null")
    return value


def _boolean(payload: Mapping[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise TypeError(f"audio payload field {key!r} must be a boolean")
    return value
