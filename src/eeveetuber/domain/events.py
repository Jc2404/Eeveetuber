"""Versioned, immutable event contracts shared by Eeveetuber components.

This is an Eeveetuber-owned contract. Provider, framework, and transport DTOs
must be translated at their adapter boundary instead of leaking into this module.
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import IntEnum, StrEnum
from types import MappingProxyType
from typing import Any, Protocol, cast, runtime_checkable
from uuid import UUID, uuid4

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type FrozenJsonValue = JsonScalar | tuple[FrozenJsonValue, ...] | Mapping[str, FrozenJsonValue]
type PayloadInput = Mapping[str, JsonValue] | EventPayload

_EVENT_TYPE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


class TrustLabel(StrEnum):
    """Origin classification, deliberately not ordered by implied trust."""

    SYSTEM = "system"
    OWNER = "owner"
    TRUSTED_OPERATOR = "trusted_operator"
    LOCAL_SENSOR = "local_sensor"
    PUBLIC_VIEWER = "public_viewer"
    RETRIEVED_DOCUMENT = "retrieved_document"
    TOOL = "tool"
    PLUGIN = "plugin"
    UNKNOWN = "unknown"


class Visibility(StrEnum):
    """Where an event or its content may be exposed."""

    PRIVATE = "private"
    OWNER_ONLY = "owner_only"
    SESSION = "session"
    STREAM_SAFE = "stream_safe"
    PUBLIC = "public"


class RetentionClass(StrEnum):
    """Persistence intent; an event bus does not make events durable itself."""

    EPHEMERAL_MEDIA = "ephemeral_media"
    OPERATIONAL_TRACE = "operational_trace"
    TRANSCRIPT = "transcript"
    AUDIT = "audit"
    DURABLE_DOMAIN = "durable_domain"


class EventPriority(IntEnum):
    """Conventional priorities. Higher values are dequeued first."""

    BACKGROUND = 10
    LOW = 25
    NORMAL = 50
    HIGH = 75
    CRITICAL = 100


@runtime_checkable
class EventPayload(Protocol):
    """Convention for typed payloads crossing the generic event boundary."""

    def to_event_payload(self) -> Mapping[str, JsonValue]:
        """Return a JSON-compatible mapping with stable field names."""


def _freeze_json(value: object, *, path: str = "payload") -> FrozenJsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJsonValue] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} keys must be strings, got {type(key).__name__}")
            frozen[key] = _freeze_json(child, path=f"{path}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(
            _freeze_json(child, path=f"{path}[{index}]")
            for index, child in enumerate(value)
        )
    raise TypeError(f"{path} is not JSON-compatible: {type(value).__name__}")


def _freeze_payload(payload: PayloadInput | Mapping[str, FrozenJsonValue]) -> Mapping[str, FrozenJsonValue]:
    raw: object
    if isinstance(payload, EventPayload):
        raw = payload.to_event_payload()
    else:
        raw = payload
    frozen = _freeze_json(raw)
    if not isinstance(frozen, Mapping):  # pragma: no cover - kept as a defensive invariant
        raise TypeError("event payload must be a mapping")
    return frozen


def _thaw_json(value: FrozenJsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(child) for child in value]
    return value


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """Immutable metadata and JSON payload for a cross-component event.

    ``sequence`` is assigned by the logical owner (normally ``SessionActor``).
    It orders accepted events but is not promised to be gap-free when queues drop
    data. Use ``monotonic_at_ms`` for local latency, never wall-clock ordering.
    """

    type: str
    payload: Mapping[str, FrozenJsonValue] = field(default_factory=dict, hash=False)
    event_id: UUID = field(default_factory=uuid4)
    schema_version: int = 1
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    monotonic_at_ms: int = field(default_factory=lambda: time.monotonic_ns() // 1_000_000)
    session_id: UUID | None = None
    actor_id: str | None = None
    correlation_id: UUID = field(default_factory=uuid4)
    causation_id: UUID | None = None
    sequence: int | None = None
    priority: int = int(EventPriority.NORMAL)
    trust: TrustLabel = TrustLabel.UNKNOWN
    visibility: Visibility = Visibility.SESSION
    retention: RetentionClass = RetentionClass.OPERATIONAL_TRACE

    def __post_init__(self) -> None:
        if not _EVENT_TYPE.fullmatch(self.type):
            raise ValueError(
                "event type must be lowercase dot/dash/underscore-separated identifiers"
            )
        if isinstance(self.schema_version, bool) or self.schema_version < 1:
            raise ValueError("schema_version must be a positive integer")
        if self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        if isinstance(self.monotonic_at_ms, bool) or self.monotonic_at_ms < 0:
            raise ValueError("monotonic_at_ms must be a non-negative integer")
        if self.actor_id is not None and not self.actor_id.strip():
            raise ValueError("actor_id cannot be blank")
        if self.sequence is not None and (
            isinstance(self.sequence, bool) or self.sequence < 0
        ):
            raise ValueError("sequence must be a non-negative integer or None")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise TypeError("priority must be an integer")
        object.__setattr__(self, "payload", _freeze_payload(self.payload))

    @classmethod
    def create(
        cls,
        event_type: str,
        payload: PayloadInput | None = None,
        *,
        event_id: UUID | None = None,
        schema_version: int = 1,
        occurred_at: datetime | None = None,
        monotonic_at_ms: int | None = None,
        session_id: UUID | None = None,
        actor_id: str | None = None,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
        sequence: int | None = None,
        priority: int | EventPriority = EventPriority.NORMAL,
        trust: TrustLabel = TrustLabel.UNKNOWN,
        visibility: Visibility = Visibility.SESSION,
        retention: RetentionClass = RetentionClass.OPERATIONAL_TRACE,
    ) -> EventEnvelope:
        """Create an envelope, defaulting a new trace's correlation to its event ID."""

        resolved_event_id = event_id or uuid4()
        return cls(
            type=event_type,
            payload=_freeze_payload(payload or {}),
            event_id=resolved_event_id,
            schema_version=schema_version,
            occurred_at=occurred_at or datetime.now(UTC),
            monotonic_at_ms=(
                monotonic_at_ms
                if monotonic_at_ms is not None
                else time.monotonic_ns() // 1_000_000
            ),
            session_id=session_id,
            actor_id=actor_id,
            correlation_id=correlation_id or resolved_event_id,
            causation_id=causation_id,
            sequence=sequence,
            priority=int(priority),
            trust=trust,
            visibility=visibility,
            retention=retention,
        )

    def with_sequence(self, sequence: int) -> EventEnvelope:
        """Return a stamped copy; envelopes are never mutated in place."""

        return replace(self, sequence=sequence)

    def for_session(self, session_id: UUID) -> EventEnvelope:
        """Bind an unscoped event, rejecting accidental cross-session reuse."""

        if self.session_id is not None and self.session_id != session_id:
            raise ValueError(
                f"event belongs to session {self.session_id}, not requested session {session_id}"
            )
        return self if self.session_id == session_id else replace(self, session_id=session_id)

    def to_dict(self) -> dict[str, JsonValue]:
        """Produce a transport-safe primitive dictionary."""

        return {
            "event_id": str(self.event_id),
            "type": self.type,
            "schema_version": self.schema_version,
            "occurred_at": self.occurred_at.isoformat(),
            "monotonic_at_ms": self.monotonic_at_ms,
            "session_id": str(self.session_id) if self.session_id else None,
            "actor_id": self.actor_id,
            "correlation_id": str(self.correlation_id),
            "causation_id": str(self.causation_id) if self.causation_id else None,
            "sequence": self.sequence,
            "priority": self.priority,
            "trust": self.trust.value,
            "visibility": self.visibility.value,
            "retention": self.retention.value,
            "payload": _thaw_json(cast(FrozenJsonValue, self.payload)),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> EventEnvelope:
        """Validate and restore an envelope at a transport/storage boundary."""

        occurred_at_raw = raw["occurred_at"]
        if not isinstance(occurred_at_raw, str):
            raise TypeError("occurred_at must be an ISO-8601 string")
        payload = raw.get("payload", {})
        if not isinstance(payload, Mapping):
            raise TypeError("payload must be a mapping")
        return cls(
            event_id=UUID(str(raw["event_id"])),
            type=str(raw["type"]),
            schema_version=int(raw["schema_version"]),
            occurred_at=datetime.fromisoformat(occurred_at_raw.replace("Z", "+00:00")),
            monotonic_at_ms=int(raw["monotonic_at_ms"]),
            session_id=UUID(str(raw["session_id"])) if raw.get("session_id") else None,
            actor_id=str(raw["actor_id"]) if raw.get("actor_id") is not None else None,
            correlation_id=UUID(str(raw["correlation_id"])),
            causation_id=(UUID(str(raw["causation_id"])) if raw.get("causation_id") else None),
            sequence=int(raw["sequence"]) if raw.get("sequence") is not None else None,
            priority=int(raw["priority"]),
            trust=TrustLabel(str(raw["trust"])),
            visibility=Visibility(str(raw["visibility"])),
            retention=RetentionClass(str(raw["retention"])),
            payload=cast(Mapping[str, FrozenJsonValue], payload),
        )
