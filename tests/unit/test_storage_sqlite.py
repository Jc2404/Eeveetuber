from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from eeveetuber.memory.context import ContextCompiler, ContextCompileRequest, ContextRevisionPin
from eeveetuber.storage import (
    EventRecord,
    MessageRecord,
    MessageRole,
    SessionRecord,
    SqliteDatabase,
    SqliteStore,
    StableIdConflict,
)

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> SqliteStore:
    value = SqliteStore(SqliteDatabase(tmp_path / "eeveetuber.db"))
    features = value.initialize()
    assert features.journal_mode == "wal"
    yield value
    value.close()


def test_messages_and_events_are_append_only_and_idempotent(store: SqliteStore) -> None:
    session = SessionRecord(
        session_id="session-1",
        namespace="eevee",
        created_at=NOW,
        metadata={"mode": "conversation"},
    )
    message = MessageRecord(
        message_id="message-1",
        session_id=session.session_id,
        sequence=1,
        role=MessageRole.USER,
        content="Remember jasmine tea",
        created_at=NOW,
        actor_id="owner-1",
    )
    event = EventRecord(
        event_id="event-1",
        event_type="turn.user.final",
        payload={"message_id": message.message_id},
        created_at=NOW,
        session_id=session.session_id,
        correlation_id="turn-1",
    )

    assert store.sessions.create(session) == session
    assert store.sessions.create(session) == session
    assert store.messages.append(message) == message
    assert store.messages.append(message) == message
    assert store.events.append(event) == event
    assert store.events.append(event) == event
    assert store.messages.get(message.message_id) == message
    assert store.events.get(event.event_id) == event

    with pytest.raises(StableIdConflict):
        store.messages.append(message.model_copy(update={"content": "different"}))


def test_fts_search_then_stable_id_window_expansion(store: SqliteStore) -> None:
    if not store.database.features.fts5:
        pytest.skip("SQLite build has no FTS5")
    store.sessions.create(
        SessionRecord(session_id="session-1", namespace="eevee", created_at=NOW)
    )
    for sequence, content in enumerate(
        [
            "hello there",
            "we discussed tea",
            "jasmine tea is my favorite",
            "please remember that",
            "goodbye",
        ],
        start=1,
    ):
        store.messages.append(
            MessageRecord(
                message_id=f"message-{sequence}",
                session_id="session-1",
                sequence=sequence,
                role=MessageRole.USER if sequence % 2 else MessageRole.ASSISTANT,
                content=content,
                created_at=NOW,
            )
        )

    hits = store.messages.search("jasmine favorite", limit=3)
    assert [hit.message_id for hit in hits] == ["message-3"]

    window = store.messages.recall_around(hits[0].message_id, before=1, after=1)
    assert [item.message_id for item in window.messages] == [
        "message-2",
        "message-3",
        "message-4",
    ]
    assert window.has_before is True
    assert window.has_after is True


def test_recent_messages_use_sequence_cursor_limit_and_chronological_output(
    store: SqliteStore,
) -> None:
    store.sessions.create(
        SessionRecord(session_id="session-recent", namespace="eevee", created_at=NOW)
    )
    for sequence in range(1, 7):
        store.messages.append(
            MessageRecord(
                message_id=f"recent-{sequence}",
                session_id="session-recent",
                sequence=sequence,
                role=MessageRole.USER,
                content=f"turn {sequence}",
                created_at=NOW,
                metadata={"generation": sequence},
            )
        )

    recent = store.messages.list_recent_before(
        "session-recent",
        before_sequence=6,
        limit=3,
    )

    assert [record.sequence for record in recent] == [3, 4, 5]
    assert all(record.sequence < 6 for record in recent)


def test_blank_assistant_message_is_invalid_before_persistence() -> None:
    with pytest.raises(ValueError, match="assistant message content cannot be blank"):
        MessageRecord(
            message_id="blank-assistant",
            session_id="session-1",
            sequence=1,
            role=MessageRole.ASSISTANT,
            content="  \n ",
            created_at=NOW,
        )


def test_context_snapshot_round_trip_preserves_revision_pin(store: SqliteStore) -> None:
    snapshot = ContextCompiler(id_factory=lambda: "snapshot-1", clock=lambda: NOW).compile(
        ContextCompileRequest(
            session_id="session-1",
            turn_id="turn-1",
            revision=ContextRevisionPin(
                namespace="eevee",
                memory_generation=17,
                canon_revision="canon-4",
                persona_revision="persona-3",
            ),
        )
    )

    store.context_snapshots.save(snapshot)
    restored = store.context_snapshots.get(snapshot.snapshot_id)
    assert restored == snapshot
    assert restored is not None
    assert restored.revision.memory_generation == 17
    assert store.context_snapshots.latest_for_namespace("eevee") == snapshot
