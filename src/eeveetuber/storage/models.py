"""SQLAlchemy 2 persistence schema for the local-first backbone."""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    pass


class SessionRow(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    closed_at: Mapped[str | None] = mapped_column(String(40))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class MessageRow(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("session_id", "sequence", name="uq_messages_session_sequence"),
        CheckConstraint("sequence >= 1", name="ck_messages_sequence_positive"),
        Index("ix_messages_session_created", "session_id", "created_at"),
    )

    message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(256))
    source_event_id: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_session_created", "session_id", "created_at"),
        Index("ix_events_correlation", "correlation_id"),
    )

    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("sessions.session_id", ondelete="SET NULL")
    )
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    causation_id: Mapped[str | None] = mapped_column(String(128))
    actor_id: Mapped[str | None] = mapped_column(String(256))


class ThreadCheckpointRow(Base):
    __tablename__ = "thread_checkpoints"
    __table_args__ = (
        UniqueConstraint("thread_id", "sequence", name="uq_checkpoints_thread_sequence"),
        CheckConstraint("sequence >= 1", name="ck_checkpoints_sequence_positive"),
    )

    checkpoint_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_checkpoint_id: Mapped[str | None] = mapped_column(String(128))
    state_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MemoryGenerationRow(Base):
    __tablename__ = "memory_generations"

    namespace: Mapped[str] = mapped_column(String(256), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MemoryRecordRow(Base):
    __tablename__ = "memory_records"
    __table_args__ = (
        Index("ix_memory_records_namespace_kind", "namespace", "kind"),
        Index("ix_memory_records_scope", "scope_kind", "scope_subject_id"),
    )

    memory_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_subject_id: Mapped[str] = mapped_column(String(256), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    current_revision_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    deleted_at: Mapped[str | None] = mapped_column(String(40))


class MemoryRevisionRow(Base):
    __tablename__ = "memory_revisions"
    __table_args__ = (
        UniqueConstraint("namespace", "generation", name="uq_memory_revision_generation"),
        Index("ix_memory_revisions_memory_generation", "memory_id", "generation"),
    )

    revision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("memory_records.memory_id", ondelete="CASCADE"), nullable=False
    )
    namespace: Mapped[str] = mapped_column(String(256), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(String(128))
    candidate_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_candidates.candidate_id", ondelete="SET NULL"),
        unique=True,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_subject_id: Mapped[str] = mapped_column(String(256), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_trust: Mapped[str] = mapped_column(String(64), nullable=False)
    source_actor_id: Mapped[str | None] = mapped_column(String(256))
    source_observed_at: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_event_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    consented: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(32), nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    valid_from: Mapped[str | None] = mapped_column(String(40))
    valid_until: Mapped[str | None] = mapped_column(String(40))
    ttl_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class MemoryCandidateRow(Base):
    __tablename__ = "memory_candidates"
    __table_args__ = (Index("ix_memory_candidates_namespace_status", "namespace", "status"),)

    candidate_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(256), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_subject_id: Mapped[str] = mapped_column(String(256), nullable=False)
    subject: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    source_trust: Mapped[str] = mapped_column(String(64), nullable=False)
    source_actor_id: Mapped[str | None] = mapped_column(String(256))
    source_observed_at: Mapped[str] = mapped_column(String(40), nullable=False)
    evidence_event_ids_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    consented: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(32), nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    valid_from: Mapped[str | None] = mapped_column(String(40))
    valid_until: Mapped[str | None] = mapped_column(String(40))
    ttl_seconds: Mapped[int | None] = mapped_column(Integer)
    base_memory_id: Mapped[str | None] = mapped_column(String(128))
    base_revision_id: Mapped[str | None] = mapped_column(String(128))
    contradiction_revision_ids_json: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)


class MemoryDecisionRow(Base):
    __tablename__ = "memory_decisions"

    decision_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("memory_candidates.candidate_id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_codes_json: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[str] = mapped_column(String(40), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(256), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(256))


class ContextSnapshotRow(Base):
    __tablename__ = "context_snapshots"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_id", name="uq_context_snapshot_turn"),
        Index("ix_context_snapshot_namespace_created", "namespace", "created_at"),
    )

    snapshot_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(256), nullable=False)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False)
    turn_id: Mapped[str] = mapped_column(String(128), nullable=False)
    memory_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    canon_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class OutboxRow(Base):
    __tablename__ = "outbox"
    __table_args__ = (Index("ix_outbox_ready", "completed_at", "available_at"),)

    outbox_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)
    available_at: Mapped[str] = mapped_column(String(40), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completed_at: Mapped[str | None] = mapped_column(String(40))
