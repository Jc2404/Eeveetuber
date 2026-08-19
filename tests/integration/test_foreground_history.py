from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from eeveetuber.adapters.fake import FakeModelProvider, FakeSpeechSynthesizer
from eeveetuber.application import (
    CharacterContextService,
    ForegroundSession,
    RecentConversationHistoryPolicy,
)
from eeveetuber.config.character import CharacterContext, CharacterProfile
from eeveetuber.config.settings import ContextBudgetSettings
from eeveetuber.memory import ContextCompiler, ContextSnapshotCache
from eeveetuber.runtime import SessionSupervisor
from eeveetuber.storage import MessageRole, SqliteDatabase, SqliteStore


async def receive_completion(session: ForegroundSession) -> None:
    while True:
        event = await asyncio.wait_for(session.receive_output(), timeout=1)
        assert event.type != "turn.failed", event.payload
        if event.type == "utterance.completed":
            return


async def make_session(
    model: FakeModelProvider,
    *,
    database_path: Path,
    history_policy: RecentConversationHistoryPolicy | None = None,
) -> tuple[ForegroundSession, SessionSupervisor, SqliteStore]:
    store = SqliteStore(SqliteDatabase(database_path))
    store.initialize()
    supervisor = SessionSupervisor()
    context_service = CharacterContextService(
        CharacterProfile(
            character_id="history-test",
            display_name="History Test",
            revision="canon-v1",
            context=CharacterContext(canon="Stay kind.", persona="Warm and concise."),
        ),
        ContextBudgetSettings(),
        ContextCompiler(),
        ContextSnapshotCache(),
        store.context_snapshots,
    )
    session = ForegroundSession(
        supervisor,
        context_service,
        store,
        model,
        FakeSpeechSynthesizer(),
        history_policy=history_policy,
    )
    await session.start()
    assert (await session.receive_output()).type == "session.ready"
    return session, supervisor, store


@pytest.mark.asyncio
async def test_second_turn_receives_persisted_prior_exchange_only(tmp_path: Path) -> None:
    model = FakeModelProvider(
        lambda request: "FIRST_REPLY_SENTINEL."
        if request.user_text == "FIRST_USER_SENTINEL"
        else "Second reply."
    )
    session, supervisor, store = await make_session(
        model,
        database_path=tmp_path / "history.db",
        history_policy=RecentConversationHistoryPolicy(
            max_messages=4,
            max_chars=2_000,
            load_timeout_ms=100,
        ),
    )
    try:
        await session.submit_text("FIRST_USER_SENTINEL")
        await receive_completion(session)
        await session.submit_text("SECOND_USER_SENTINEL")
        await receive_completion(session)

        assert len(model.requests) == 2
        second = model.requests[1]
        assert second.user_text == "SECOND_USER_SENTINEL"
        assert "FIRST_USER_SENTINEL" in second.system_context
        assert "FIRST_REPLY_SENTINEL" in second.system_context
        assert "SECOND_USER_SENTINEL" not in second.system_context
        assert second.metadata["history_message_count"] == "2"
        assert second.generation == 2
    finally:
        await session.stop()
        await supervisor.shutdown()
        store.close()


@pytest.mark.asyncio
async def test_empty_model_output_never_persists_blank_assistant_message(tmp_path: Path) -> None:
    session, supervisor, store = await make_session(
        FakeModelProvider(""),
        database_path=tmp_path / "empty.db",
    )
    try:
        await session.submit_text("Keep only this user message")
        while True:
            terminal = await asyncio.wait_for(session.receive_output(), timeout=1)
            if terminal.type == "turn.failed":
                break
        assert terminal.payload["error_type"] == "ModelEmptyOutput"
        for _attempt in range(50):
            records = store.messages.list_session(str(session.session_id))
            if records:
                break
            await asyncio.sleep(0.002)

        assert [(record.role, record.content) for record in records] == [
            (MessageRole.USER, "Keep only this user message")
        ]
    finally:
        await session.stop()
        await supervisor.shutdown()
        store.close()


@pytest.mark.asyncio
async def test_slow_history_read_abandons_at_deadline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    model = FakeModelProvider("Fast fallback.")
    session, supervisor, store = await make_session(
        model,
        database_path=tmp_path / "slow.db",
        history_policy=RecentConversationHistoryPolicy(load_timeout_ms=10),
    )
    original = store.messages.list_recent_before

    def slow_read(*args: object, **kwargs: object) -> object:
        time.sleep(0.2)
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(store.messages, "list_recent_before", slow_read)
    try:
        started = asyncio.get_running_loop().time()
        await session.submit_text("Do not wait for history")
        await receive_completion(session)
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 0.15
        assert model.requests[0].metadata["history_message_count"] == "0"
    finally:
        await session.stop()
        await supervisor.shutdown()
        store.close()
