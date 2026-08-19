from uuid import uuid4

import pytest

from eeveetuber.application.context_service import CharacterContextService
from eeveetuber.config.character import CharacterContext, CharacterProfile
from eeveetuber.config.settings import ContextBudgetSettings
from eeveetuber.memory.context import ContextCompiler, ContextSnapshotCache
from eeveetuber.storage import SqliteDatabase, SqliteStore


@pytest.mark.asyncio
async def test_snapshot_is_hot_before_background_persistence() -> None:
    database = SqliteDatabase(":memory:")
    store = SqliteStore(database)
    store.initialize()
    cache = ContextSnapshotCache()
    service = CharacterContextService(
        CharacterProfile(
            character_id="test",
            display_name="Test",
            revision="canon-v1",
            context=CharacterContext(canon="Owner canon", persona="Warm and concise"),
        ),
        ContextBudgetSettings(),
        ContextCompiler(id_factory=lambda: "ctx-test"),
        cache,
        store.context_snapshots,
    )

    snapshot = service.compile_for_turn(uuid4(), uuid4())

    assert cache.latest("character:test") is snapshot
    assert store.context_snapshots.get("ctx-test") is None

    await service.persist_snapshot(snapshot)
    assert store.context_snapshots.get("ctx-test") == snapshot
    database.close()

