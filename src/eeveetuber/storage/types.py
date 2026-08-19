"""Storage-facing immutable values shared by repository consumers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from eeveetuber.memory.models import FrozenModel


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class SessionRecord(FrozenModel):
    session_id: str
    namespace: str
    created_at: datetime
    closed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageRecord(FrozenModel):
    message_id: str
    session_id: str
    sequence: int = Field(ge=1)
    role: MessageRole
    content: str
    created_at: datetime
    actor_id: str | None = None
    source_event_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_blank_conversation_content(self) -> MessageRecord:
        if self.role in {MessageRole.USER, MessageRole.ASSISTANT} and not self.content.strip():
            raise ValueError(f"{self.role.value} message content cannot be blank")
        return self


class EventRecord(FrozenModel):
    event_id: str
    event_type: str
    payload: dict[str, Any]
    created_at: datetime
    session_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    actor_id: str | None = None


class RecallWindow(FrozenModel):
    needle_message_id: str
    session_id: str
    messages: tuple[MessageRecord, ...]
    has_before: bool
    has_after: bool


class MessageSearchHit(FrozenModel):
    message_id: str
    session_id: str
    content: str
    score: float


class ThreadCheckpoint(FrozenModel):
    checkpoint_id: str
    thread_id: str
    sequence: int = Field(ge=1)
    state: dict[str, Any]
    created_at: datetime
    parent_checkpoint_id: str | None = None


class OutboxItem(FrozenModel):
    outbox_id: str
    topic: str
    payload: dict[str, Any]
    created_at: datetime
    available_at: datetime
    attempts: int = Field(default=0, ge=0)
    completed_at: datetime | None = None
