"""Append-only sessions, messages, and event repositories."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy import exists, select, text
from sqlalchemy.orm import Session, sessionmaker

from eeveetuber.storage._serialization import decode_datetime, encode_datetime
from eeveetuber.storage.errors import RecordNotFound, SearchUnavailable, StableIdConflict
from eeveetuber.storage.models import EventRow, MessageRow, SessionRow
from eeveetuber.storage.search import safe_fts_query
from eeveetuber.storage.types import (
    EventRecord,
    MessageRecord,
    MessageRole,
    MessageSearchHit,
    RecallWindow,
    SessionRecord,
)


class SessionRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def create(self, record: SessionRecord) -> SessionRecord:
        with self._session_factory.begin() as session:
            existing = session.get(SessionRow, record.session_id)
            if existing is not None:
                stored = _session_record(existing)
                if stored != record:
                    raise StableIdConflict(f"session ID {record.session_id!r} has different data")
                return stored
            session.add(
                SessionRow(
                    session_id=record.session_id,
                    namespace=record.namespace,
                    created_at=encode_datetime(record.created_at),
                    closed_at=(encode_datetime(record.closed_at) if record.closed_at else None),
                    metadata_json=record.metadata,
                )
            )
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        with self._session_factory() as session:
            row = session.get(SessionRow, session_id)
            return _session_record(row) if row is not None else None


class MessageRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        fts_available: Callable[[], bool],
    ) -> None:
        self._session_factory = session_factory
        self._fts_available = fts_available

    def append(self, record: MessageRecord) -> MessageRecord:
        with self._session_factory.begin() as session:
            if session.get(SessionRow, record.session_id) is None:
                raise RecordNotFound(f"session {record.session_id!r} does not exist")
            existing = session.get(MessageRow, record.message_id)
            if existing is not None:
                stored = _message_record(existing)
                if stored != record:
                    raise StableIdConflict(f"message ID {record.message_id!r} has different data")
                return stored
            session.add(
                MessageRow(
                    message_id=record.message_id,
                    session_id=record.session_id,
                    sequence=record.sequence,
                    role=record.role.value,
                    content=record.content,
                    created_at=encode_datetime(record.created_at),
                    actor_id=record.actor_id,
                    source_event_id=record.source_event_id,
                    metadata_json=record.metadata,
                )
            )
            session.flush()
            if self._fts_available():
                session.execute(
                    text(
                        "INSERT INTO messages_fts(message_id, session_id, content) "
                        "VALUES (:message_id, :session_id, :content)"
                    ),
                    {
                        "message_id": record.message_id,
                        "session_id": record.session_id,
                        "content": record.content,
                    },
                )
        return record

    def get(self, message_id: str) -> MessageRecord | None:
        with self._session_factory() as session:
            row = session.get(MessageRow, message_id)
            return _message_record(row) if row is not None else None

    def list_session(self, session_id: str, *, limit: int = 100) -> Sequence[MessageRecord]:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        with self._session_factory() as session:
            rows = session.scalars(
                select(MessageRow)
                .where(MessageRow.session_id == session_id)
                .order_by(MessageRow.sequence)
                .limit(limit)
            ).all()
            return tuple(_message_record(row) for row in rows)

    def list_recent_before(
        self,
        session_id: str,
        *,
        before_sequence: int,
        limit: int,
    ) -> Sequence[MessageRecord]:
        """Return the newest bounded prefix before a stable sequence cursor.

        The SQL query uses the unique ``(session_id, sequence)`` index and applies
        ``LIMIT`` before materialization. Results are reversed back into prompt-safe
        chronological order.
        """

        if before_sequence < 1:
            raise ValueError("before_sequence must be positive")
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        with self._session_factory() as session:
            rows = list(
                session.scalars(
                    select(MessageRow)
                    .where(
                        MessageRow.session_id == session_id,
                        MessageRow.sequence < before_sequence,
                    )
                    .order_by(MessageRow.sequence.desc())
                    .limit(limit)
                ).all()
            )
            rows.reverse()
            return tuple(_message_record(row) for row in rows)

    def search(self, query: str, *, session_id: str | None = None, limit: int = 5) -> Sequence[MessageSearchHit]:
        if not self._fts_available():
            raise SearchUnavailable("this SQLite build does not provide FTS5")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        match_query = safe_fts_query(query)
        if not match_query:
            return ()
        session_clause = "AND session_id = :session_id" if session_id else ""
        statement = text(
            "SELECT message_id, session_id, content, bm25(messages_fts) AS rank "
            "FROM messages_fts WHERE messages_fts MATCH :query "
            f"{session_clause} ORDER BY rank LIMIT :limit"
        )
        parameters: dict[str, object] = {"query": match_query, "limit": limit}
        if session_id:
            parameters["session_id"] = session_id
        with self._session_factory() as session:
            rows = session.execute(statement, parameters).mappings().all()
            return tuple(
                MessageSearchHit(
                    message_id=str(row["message_id"]),
                    session_id=str(row["session_id"]),
                    content=str(row["content"]),
                    score=-float(row["rank"]),
                )
                for row in rows
            )

    def recall_around(
        self,
        message_id: str,
        *,
        before: int = 2,
        after: int = 2,
    ) -> RecallWindow:
        if not 0 <= before <= 50 or not 0 <= after <= 50:
            raise ValueError("before and after must be between 0 and 50")
        with self._session_factory() as session:
            needle = session.get(MessageRow, message_id)
            if needle is None:
                raise RecordNotFound(f"message {message_id!r} does not exist")
            low = needle.sequence - before
            high = needle.sequence + after
            rows = session.scalars(
                select(MessageRow)
                .where(
                    MessageRow.session_id == needle.session_id,
                    MessageRow.sequence >= low,
                    MessageRow.sequence <= high,
                )
                .order_by(MessageRow.sequence)
            ).all()
            has_before = bool(
                session.scalar(
                    select(exists().where(
                        MessageRow.session_id == needle.session_id,
                        MessageRow.sequence < low,
                    ))
                )
            )
            has_after = bool(
                session.scalar(
                    select(exists().where(
                        MessageRow.session_id == needle.session_id,
                        MessageRow.sequence > high,
                    ))
                )
            )
            return RecallWindow(
                needle_message_id=message_id,
                session_id=needle.session_id,
                messages=tuple(_message_record(row) for row in rows),
                has_before=has_before,
                has_after=has_after,
            )


class EventRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def append(self, record: EventRecord) -> EventRecord:
        with self._session_factory.begin() as session:
            existing = session.get(EventRow, record.event_id)
            if existing is not None:
                stored = _event_record(existing)
                if stored != record:
                    raise StableIdConflict(f"event ID {record.event_id!r} has different data")
                return stored
            if record.session_id and session.get(SessionRow, record.session_id) is None:
                raise RecordNotFound(f"session {record.session_id!r} does not exist")
            session.add(
                EventRow(
                    event_id=record.event_id,
                    event_type=record.event_type,
                    payload_json=record.payload,
                    created_at=encode_datetime(record.created_at),
                    session_id=record.session_id,
                    correlation_id=record.correlation_id,
                    causation_id=record.causation_id,
                    actor_id=record.actor_id,
                )
            )
        return record

    def get(self, event_id: str) -> EventRecord | None:
        with self._session_factory() as session:
            row = session.get(EventRow, event_id)
            return _event_record(row) if row is not None else None


def _session_record(row: SessionRow) -> SessionRecord:
    return SessionRecord(
        session_id=row.session_id,
        namespace=row.namespace,
        created_at=decode_datetime(row.created_at),
        closed_at=decode_datetime(row.closed_at) if row.closed_at else None,
        metadata=row.metadata_json,
    )


def _message_record(row: MessageRow) -> MessageRecord:
    return MessageRecord(
        message_id=row.message_id,
        session_id=row.session_id,
        sequence=row.sequence,
        role=MessageRole(row.role),
        content=row.content,
        created_at=decode_datetime(row.created_at),
        actor_id=row.actor_id,
        source_event_id=row.source_event_id,
        metadata=row.metadata_json,
    )


def _event_record(row: EventRow) -> EventRecord:
    return EventRecord(
        event_id=row.event_id,
        event_type=row.event_type,
        payload=row.payload_json,
        created_at=decode_datetime(row.created_at),
        session_id=row.session_id,
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        actor_id=row.actor_id,
    )
