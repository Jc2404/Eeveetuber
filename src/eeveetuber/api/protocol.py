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
    segment_id: UUID
    chunk_index: int = Field(ge=0)
    state: PlaybackState
    client_monotonic_ms: int = Field(ge=0)
    played_ms: int | None = Field(default=None, ge=0)
    detail: str | None = Field(default=None, max_length=500)


type ClientMessage = Annotated[
    TextTurnMessage
    | CancelTurnMessage
    | PingMessage
    | OperatorControlMessage
    | PlaybackAckMessage,
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
