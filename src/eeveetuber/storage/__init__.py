"""Local persistence backbone.

Call ``SqliteStore(SqliteDatabase(path)).initialize()`` during application
startup.  Realtime code should normally read the in-memory ContextSnapshotCache;
database work remains explicit and can be scheduled outside the audio loop.
"""

from eeveetuber.storage.database import DatabaseFeatures, SqliteDatabase
from eeveetuber.storage.errors import (
    InvalidPromotion,
    OptimisticConcurrencyError,
    RecordNotFound,
    SearchUnavailable,
    StableIdConflict,
    StorageError,
)
from eeveetuber.storage.ids import (
    new_candidate_id,
    new_checkpoint_id,
    new_event_id,
    new_memory_id,
    new_message_id,
    new_session_id,
    new_stable_id,
)
from eeveetuber.storage.store import SqliteStore
from eeveetuber.storage.types import (
    EventRecord,
    MessageRecord,
    MessageRole,
    RecallWindow,
    SessionRecord,
    ThreadCheckpoint,
)

__all__ = [
    "DatabaseFeatures",
    "EventRecord",
    "InvalidPromotion",
    "MessageRecord",
    "MessageRole",
    "OptimisticConcurrencyError",
    "RecallWindow",
    "RecordNotFound",
    "SearchUnavailable",
    "SessionRecord",
    "SqliteDatabase",
    "SqliteStore",
    "StableIdConflict",
    "StorageError",
    "ThreadCheckpoint",
    "new_candidate_id",
    "new_checkpoint_id",
    "new_event_id",
    "new_memory_id",
    "new_message_id",
    "new_session_id",
    "new_stable_id",
]
