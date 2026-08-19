"""Versioned WebSocket DTOs; domain/provider objects never cross this boundary directly."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


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


type ClientMessage = Annotated[
    TextTurnMessage | CancelTurnMessage | PingMessage | OperatorControlMessage,
    Field(discriminator="type"),
]

_CLIENT_MESSAGE_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


def parse_client_message(raw: str | bytes) -> ClientMessage:
    return _CLIENT_MESSAGE_ADAPTER.validate_json(raw)


class ServerMessage(BaseModel):
    """Stable server envelope whose data schema is selected by ``type``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[1] = 1
    message_id: UUID = Field(default_factory=uuid4)
    type: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    session_id: UUID
    correlation_id: UUID
    causation_id: UUID | None = None
    sequence: int = Field(ge=0)
    generation: int = Field(ge=0)
    data: dict[str, Any] = Field(default_factory=dict)
