"""Persistence for immutable compiled context snapshots."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from eeveetuber.memory.context import ContextSnapshot
from eeveetuber.storage._serialization import encode_datetime
from eeveetuber.storage.errors import StableIdConflict
from eeveetuber.storage.models import ContextSnapshotRow


class SqliteContextSnapshotRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def save(self, snapshot: ContextSnapshot) -> ContextSnapshot:
        with self._session_factory.begin() as session:
            existing = session.get(ContextSnapshotRow, snapshot.snapshot_id)
            if existing is not None:
                stored = _snapshot(existing)
                if stored != snapshot:
                    raise StableIdConflict(
                        f"context snapshot ID {snapshot.snapshot_id!r} has different data"
                    )
                return stored
            turn_existing = session.scalar(
                select(ContextSnapshotRow).where(
                    ContextSnapshotRow.session_id == snapshot.session_id,
                    ContextSnapshotRow.turn_id == snapshot.turn_id,
                )
            )
            if turn_existing is not None:
                stored = _snapshot(turn_existing)
                if stored != snapshot:
                    raise StableIdConflict(
                        f"turn {snapshot.turn_id!r} already pins snapshot "
                        f"{stored.snapshot_id!r}"
                    )
                return stored
            session.add(
                ContextSnapshotRow(
                    snapshot_id=snapshot.snapshot_id,
                    namespace=snapshot.revision.namespace,
                    session_id=snapshot.session_id,
                    turn_id=snapshot.turn_id,
                    memory_generation=snapshot.revision.memory_generation,
                    canon_revision=snapshot.revision.canon_revision,
                    payload_json=snapshot.model_dump(mode="json"),
                    created_at=encode_datetime(snapshot.created_at),
                )
            )
        return snapshot

    def get(self, snapshot_id: str) -> ContextSnapshot | None:
        with self._session_factory() as session:
            row = session.get(ContextSnapshotRow, snapshot_id)
            return _snapshot(row) if row is not None else None

    def latest_for_namespace(self, namespace: str) -> ContextSnapshot | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ContextSnapshotRow)
                .where(ContextSnapshotRow.namespace == namespace)
                .order_by(
                    ContextSnapshotRow.memory_generation.desc(),
                    ContextSnapshotRow.created_at.desc(),
                )
                .limit(1)
            )
            return _snapshot(row) if row is not None else None


def _snapshot(row: ContextSnapshotRow) -> ContextSnapshot:
    return ContextSnapshot.model_validate(row.payload_json)

