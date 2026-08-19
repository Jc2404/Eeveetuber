"""Append-only thread checkpoint and transactional outbox repositories."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from eeveetuber.storage._serialization import (
    decode_datetime,
    decode_optional_datetime,
    encode_datetime,
    encode_optional_datetime,
)
from eeveetuber.storage.errors import StableIdConflict
from eeveetuber.storage.models import OutboxRow, ThreadCheckpointRow
from eeveetuber.storage.types import OutboxItem, ThreadCheckpoint


class CheckpointRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def append(self, checkpoint: ThreadCheckpoint) -> ThreadCheckpoint:
        with self._session_factory.begin() as session:
            existing = session.get(ThreadCheckpointRow, checkpoint.checkpoint_id)
            if existing is not None:
                stored = _checkpoint(existing)
                if stored != checkpoint:
                    raise StableIdConflict(
                        f"checkpoint ID {checkpoint.checkpoint_id!r} has different data"
                    )
                return stored
            session.add(
                ThreadCheckpointRow(
                    checkpoint_id=checkpoint.checkpoint_id,
                    thread_id=checkpoint.thread_id,
                    sequence=checkpoint.sequence,
                    parent_checkpoint_id=checkpoint.parent_checkpoint_id,
                    state_json=checkpoint.state,
                    created_at=encode_datetime(checkpoint.created_at),
                )
            )
        return checkpoint

    def latest(self, thread_id: str) -> ThreadCheckpoint | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ThreadCheckpointRow)
                .where(ThreadCheckpointRow.thread_id == thread_id)
                .order_by(ThreadCheckpointRow.sequence.desc())
                .limit(1)
            )
            return _checkpoint(row) if row is not None else None


class OutboxRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def enqueue(self, item: OutboxItem) -> OutboxItem:
        with self._session_factory.begin() as session:
            existing = session.get(OutboxRow, item.outbox_id)
            if existing is not None:
                stored = _outbox(existing)
                if stored != item:
                    raise StableIdConflict(f"outbox ID {item.outbox_id!r} has different data")
                return stored
            session.add(
                OutboxRow(
                    outbox_id=item.outbox_id,
                    topic=item.topic,
                    payload_json=item.payload,
                    created_at=encode_datetime(item.created_at),
                    available_at=encode_datetime(item.available_at),
                    attempts=item.attempts,
                    completed_at=encode_optional_datetime(item.completed_at),
                )
            )
        return item

    def ready(self, now: datetime, *, limit: int = 100) -> Sequence[OutboxItem]:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        encoded_now = encode_datetime(now)
        with self._session_factory() as session:
            rows = session.scalars(
                select(OutboxRow)
                .where(
                    OutboxRow.completed_at.is_(None),
                    OutboxRow.available_at <= encoded_now,
                )
                .order_by(OutboxRow.available_at, OutboxRow.outbox_id)
                .limit(limit)
            ).all()
            return tuple(_outbox(row) for row in rows)


def _checkpoint(row: ThreadCheckpointRow) -> ThreadCheckpoint:
    return ThreadCheckpoint(
        checkpoint_id=row.checkpoint_id,
        thread_id=row.thread_id,
        sequence=row.sequence,
        parent_checkpoint_id=row.parent_checkpoint_id,
        state=row.state_json,
        created_at=decode_datetime(row.created_at),
    )


def _outbox(row: OutboxRow) -> OutboxItem:
    return OutboxItem(
        outbox_id=row.outbox_id,
        topic=row.topic,
        payload=row.payload_json,
        created_at=decode_datetime(row.created_at),
        available_at=decode_datetime(row.available_at),
        attempts=row.attempts,
        completed_at=decode_optional_datetime(row.completed_at),
    )

