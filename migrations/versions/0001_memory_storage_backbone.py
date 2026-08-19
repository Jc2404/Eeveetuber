"""Create the P0 memory and storage backbone.

Revision ID: 0001_memory_storage
Revises: None
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_memory_storage"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("session_id", sa.String(128), primary_key=True),
        sa.Column("namespace", sa.String(256), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("closed_at", sa.String(40)),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
    )
    op.create_index("ix_sessions_namespace", "sessions", ["namespace"])

    op.create_table(
        "messages",
        sa.Column("message_id", sa.String(128), primary_key=True),
        sa.Column(
            "session_id",
            sa.String(128),
            sa.ForeignKey("sessions.session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("actor_id", sa.String(256)),
        sa.Column("source_event_id", sa.String(128)),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_messages_sequence_positive"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_messages_session_sequence"),
    )
    op.create_index("ix_messages_session_created", "messages", ["session_id", "created_at"])

    op.create_table(
        "events",
        sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column(
            "session_id",
            sa.String(128),
            sa.ForeignKey("sessions.session_id", ondelete="SET NULL"),
        ),
        sa.Column("correlation_id", sa.String(128)),
        sa.Column("causation_id", sa.String(128)),
        sa.Column("actor_id", sa.String(256)),
    )
    op.create_index("ix_events_event_type", "events", ["event_type"])
    op.create_index("ix_events_session_created", "events", ["session_id", "created_at"])
    op.create_index("ix_events_correlation", "events", ["correlation_id"])

    op.create_table(
        "thread_checkpoints",
        sa.Column("checkpoint_id", sa.String(128), primary_key=True),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(128)),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_checkpoints_sequence_positive"),
        sa.UniqueConstraint("thread_id", "sequence", name="uq_checkpoints_thread_sequence"),
    )
    op.create_index("ix_thread_checkpoints_thread_id", "thread_checkpoints", ["thread_id"])

    op.create_table(
        "memory_generations",
        sa.Column("namespace", sa.String(256), primary_key=True),
        sa.Column("generation", sa.Integer(), nullable=False),
    )
    op.create_table(
        "memory_candidates",
        sa.Column("candidate_id", sa.String(128), primary_key=True),
        sa.Column("namespace", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("scope_kind", sa.String(64), nullable=False),
        sa.Column("scope_subject_id", sa.String(256), nullable=False),
        sa.Column("subject", sa.String(512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(512), nullable=False),
        sa.Column("source_trust", sa.String(64), nullable=False),
        sa.Column("source_actor_id", sa.String(256)),
        sa.Column("source_observed_at", sa.String(40), nullable=False),
        sa.Column("evidence_event_ids_json", sa.JSON(), nullable=False),
        sa.Column("consented", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("sensitivity", sa.String(32), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("valid_from", sa.String(40)),
        sa.Column("valid_until", sa.String(40)),
        sa.Column("ttl_seconds", sa.Integer()),
        sa.Column("base_memory_id", sa.String(128)),
        sa.Column("base_revision_id", sa.String(128)),
        sa.Column("contradiction_revision_ids_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
    )
    op.create_index(
        "ix_memory_candidates_namespace_status",
        "memory_candidates",
        ["namespace", "status"],
    )

    op.create_table(
        "memory_records",
        sa.Column("memory_id", sa.String(128), primary_key=True),
        sa.Column("namespace", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("scope_kind", sa.String(64), nullable=False),
        sa.Column("scope_subject_id", sa.String(256), nullable=False),
        sa.Column("subject", sa.String(512), nullable=False),
        sa.Column("current_revision_id", sa.String(128), nullable=False, unique=True),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("deleted_at", sa.String(40)),
    )
    op.create_index(
        "ix_memory_records_namespace_kind", "memory_records", ["namespace", "kind"]
    )
    op.create_index(
        "ix_memory_records_scope", "memory_records", ["scope_kind", "scope_subject_id"]
    )

    op.create_table(
        "memory_revisions",
        sa.Column("revision_id", sa.String(128), primary_key=True),
        sa.Column(
            "memory_id",
            sa.String(128),
            sa.ForeignKey("memory_records.memory_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("namespace", sa.String(256), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("parent_revision_id", sa.String(128)),
        sa.Column(
            "candidate_id",
            sa.String(128),
            sa.ForeignKey("memory_candidates.candidate_id", ondelete="SET NULL"),
            unique=True,
        ),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("scope_kind", sa.String(64), nullable=False),
        sa.Column("scope_subject_id", sa.String(256), nullable=False),
        sa.Column("subject", sa.String(512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(512), nullable=False),
        sa.Column("source_trust", sa.String(64), nullable=False),
        sa.Column("source_actor_id", sa.String(256)),
        sa.Column("source_observed_at", sa.String(40), nullable=False),
        sa.Column("evidence_event_ids_json", sa.JSON(), nullable=False),
        sa.Column("consented", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("sensitivity", sa.String(32), nullable=False),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("valid_from", sa.String(40)),
        sa.Column("valid_until", sa.String(40)),
        sa.Column("ttl_seconds", sa.Integer()),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("namespace", "generation", name="uq_memory_revision_generation"),
    )
    op.create_index(
        "ix_memory_revisions_memory_generation",
        "memory_revisions",
        ["memory_id", "generation"],
    )

    op.create_table(
        "memory_decisions",
        sa.Column("decision_id", sa.String(128), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.String(128),
            sa.ForeignKey("memory_candidates.candidate_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("reason_codes_json", sa.JSON(), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("decided_at", sa.String(40), nullable=False),
        sa.Column("decided_by", sa.String(256), nullable=False),
        sa.Column("approved_by", sa.String(256)),
    )
    op.create_index("ix_memory_decisions_candidate_id", "memory_decisions", ["candidate_id"])

    op.create_table(
        "context_snapshots",
        sa.Column("snapshot_id", sa.String(128), primary_key=True),
        sa.Column("namespace", sa.String(256), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("turn_id", sa.String(128), nullable=False),
        sa.Column("memory_generation", sa.Integer(), nullable=False),
        sa.Column("canon_revision", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("session_id", "turn_id", name="uq_context_snapshot_turn"),
    )
    op.create_index(
        "ix_context_snapshot_namespace_created",
        "context_snapshots",
        ["namespace", "created_at"],
    )

    op.create_table(
        "outbox",
        sa.Column("outbox_id", sa.String(128), primary_key=True),
        sa.Column("topic", sa.String(128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("available_at", sa.String(40), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("completed_at", sa.String(40)),
    )
    op.create_index("ix_outbox_ready", "outbox", ["completed_at", "available_at"])

    op.execute(
        "CREATE VIRTUAL TABLE messages_fts USING fts5("
        "message_id UNINDEXED, session_id UNINDEXED, content, tokenize='unicode61')"
    )
    op.execute(
        "CREATE VIRTUAL TABLE memory_fts USING fts5("
        "revision_id UNINDEXED, memory_id UNINDEXED, namespace UNINDEXED, "
        "subject, content, tokenize='unicode61')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS memory_fts")
    op.execute("DROP TABLE IF EXISTS messages_fts")
    op.drop_table("outbox")
    op.drop_table("context_snapshots")
    op.drop_table("memory_decisions")
    op.drop_table("memory_revisions")
    op.drop_table("memory_records")
    op.drop_table("memory_candidates")
    op.drop_table("memory_generations")
    op.drop_table("thread_checkpoints")
    op.drop_table("events")
    op.drop_table("messages")
    op.drop_table("sessions")
