"""Composition root for the local SQLite repository implementations."""

from __future__ import annotations

from eeveetuber.storage.checkpoint_repository import CheckpointRepository, OutboxRepository
from eeveetuber.storage.context_repository import SqliteContextSnapshotRepository
from eeveetuber.storage.conversation_repository import (
    EventRepository,
    MessageRepository,
    SessionRepository,
)
from eeveetuber.storage.database import DatabaseFeatures, SqliteDatabase
from eeveetuber.storage.memory_repository import SqliteMemoryRepository


class SqliteStore:
    def __init__(self, database: SqliteDatabase) -> None:
        self.database = database
        factory = database.session_factory

        def fts_available() -> bool:
            return database.features.fts5

        self.sessions = SessionRepository(factory)
        self.messages = MessageRepository(factory, fts_available=fts_available)
        self.events = EventRepository(factory)
        self.checkpoints = CheckpointRepository(factory)
        self.outbox = OutboxRepository(factory)
        self.memories = SqliteMemoryRepository(factory, fts_available=fts_available)
        self.context_snapshots = SqliteContextSnapshotRepository(factory)

    def initialize(self) -> DatabaseFeatures:
        return self.database.initialize()

    def close(self) -> None:
        self.database.close()
