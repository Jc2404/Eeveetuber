"""Versioned WebSocket DTOs; domain/provider objects never cross this boundary directly."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

WEBSOCKET_SUBPROTOCOL_JSON = "eeveetuber.v1.json"
WEBSOCKET_SUBPROTOCOL_BINARY_AUDIO = "eeveetuber.v1.binary-audio"


class _ClientMessageBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[1] = 1
    message_id: UUID = Field(default_factory=uuid4)


class TextTurnMessage(_ClientMessageBase):
    type: Literal["turn.text"]
    text: str = Field(min_length=1, max_length=32_000)


class CancelTurnMessage(_ClientMessageBase):
    type: Literal["turn.cancel"]
    reason: str = Field(default="user_requested", max_length=200)


class VoiceCaptureStartMessage(_ClientMessageBase):
    """Open one explicitly identified PCM capture stream on this WebSocket."""

    type: Literal["voice.capture.start"]
    stream_id: UUID
    sample_rate_hz: int = Field(ge=8_000, le=192_000)
    channels: int = Field(default=1, ge=1, le=8)
    encoding: Literal["pcm_s16le"] = "pcm_s16le"


class VoiceCaptureStopMessage(_ClientMessageBase):
    """Flush and close the matching capture stream after all prior binary frames."""

    type: Literal["voice.capture.stop"]
    stream_id: UUID
    reason: str = Field(default="operator_requested", min_length=1, max_length=200)


class PingMessage(_ClientMessageBase):
    type: Literal["ping"]


class OperatorControlMessage(_ClientMessageBase):
    type: Literal["operator.control"]
    action: Literal["stop_speech", "neutral_avatar", "kill_session"]


class PlaybackState(StrEnum):
    """Browser playback lifecycle reported for a single audio frame/event."""

    QUEUED = "queued"
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"


class PlaybackAckMessage(_ClientMessageBase):
    """Correlated client acknowledgement; never infer playback from send success."""

    type: Literal["playback.ack"]
    session_id: UUID
    audio_event_id: UUID
    generation: int = Field(ge=0)
    event_sequence: int = Field(ge=0)
    turn_id: UUID | None = None
    segment_id: UUID
    chunk_index: int = Field(ge=0)
    is_final: bool = False
    state: PlaybackState
    client_monotonic_ms: int = Field(ge=0)
    played_ms: int | None = Field(default=None, ge=0)
    detail: str | None = Field(default=None, max_length=500)


class AvatarRendererState(StrEnum):
    """Browser renderer availability; dialogue remains usable in every state."""

    READY = "ready"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class AvatarRendererStatusMessage(_ClientMessageBase):
    """Renderer handshake/health reported after processing session capabilities."""

    type: Literal["avatar.renderer.status"]
    avatar_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_-]+$")
    revision: str = Field(min_length=1, max_length=128)
    state: AvatarRendererState
    client_monotonic_ms: int = Field(ge=0)
    detail: str | None = Field(default=None, max_length=500)


class AvatarPresentationAckState(StrEnum):
    APPLIED = "applied"
    IGNORED = "ignored"
    FAILED = "failed"


class AvatarPresentationAckMessage(_ClientMessageBase):
    """Correlate one browser presentation result with its scheduler command."""

    type: Literal["avatar.presentation.ack"]
    avatar_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9_-]+$")
    revision: str = Field(min_length=1, max_length=128)
    command_id: UUID
    generation: int = Field(ge=0)
    scheduler_sequence: int = Field(ge=0)
    state: AvatarPresentationAckState
    client_monotonic_ms: int = Field(ge=0)
    detail: str | None = Field(default=None, max_length=500)


type ClientMessage = Annotated[
    TextTurnMessage
    | CancelTurnMessage
    | VoiceCaptureStartMessage
    | VoiceCaptureStopMessage
    | PingMessage
    | OperatorControlMessage
    | PlaybackAckMessage
    | AvatarRendererStatusMessage
    | AvatarPresentationAckMessage,
    Field(discriminator="type"),
]

_CLIENT_MESSAGE_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_client_message(raw: str | bytes) -> ClientMessage:
    return _CLIENT_MESSAGE_ADAPTER.validate_json(raw)


class _ServerMessageBase(BaseModel):
    """Stable correlation and ordering fields shared by every server message."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[1] = 1
    message_id: UUID = Field(default_factory=uuid4)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: UUID
    correlation_id: UUID
    causation_id: UUID | None = None
    sequence: int = Field(ge=0)
    generation: int = Field(ge=0)


class ServerMessage(_ServerMessageBase):
    """Generic domain-event transport retained for version-one compatibility."""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class StatusScope(StrEnum):
    SERVER = "server"
    SESSION = "session"
    TURN = "turn"
    AUDIO = "audio"
    AVATAR = "avatar"


class StatusCode(StrEnum):
    CONNECTED = "connected"
    READY = "ready"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    WAITING_APPROVAL = "waiting_approval"
    INTERRUPTING = "interrupting"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"


class StatusMessage(_ServerMessageBase):
    """Typed status contract for operator UI state, health, and degradation."""

    type: Literal["session.status"] = "session.status"
    scope: StatusScope = StatusScope.SESSION
    status: StatusCode
    detail: str | None = Field(default=None, max_length=1_000)
    recoverable: bool = True
