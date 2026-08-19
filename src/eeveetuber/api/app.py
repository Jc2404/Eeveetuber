"""FastAPI composition root and versioned WebSocket vertical tracer."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from eeveetuber.adapters.fake import FakeModelProvider, FakeSpeechSynthesizer
from eeveetuber.api.protocol import (
    CancelTurnMessage,
    OperatorControlMessage,
    PingMessage,
    TextTurnMessage,
    parse_client_message,
)
from eeveetuber.api.translation import event_to_server_message
from eeveetuber.application import CharacterContextService, ForegroundSession
from eeveetuber.config import AppSettings, CharacterProfile, get_settings, load_character_profile
from eeveetuber.dialogue.ports import ModelProvider, SpeechSynthesizer
from eeveetuber.memory.context import ContextCompiler, ContextSnapshotCache
from eeveetuber.observability import get_logger
from eeveetuber.runtime import MailboxClosed, SessionSupervisor
from eeveetuber.storage import SqliteDatabase, SqliteStore

ModelFactory = Callable[[], ModelProvider]
SpeechFactory = Callable[[], SpeechSynthesizer]


@dataclass(slots=True)
class AppResources:
    settings: AppSettings
    profile: CharacterProfile
    store: SqliteStore
    supervisor: SessionSupervisor
    context_service: CharacterContextService
    model_factory: ModelFactory
    speech_factory: SpeechFactory


def _default_profile_path() -> Path:
    return Path(__file__).resolve().parents[3] / "profiles" / "characters" / "default.toml"


def create_app(
    settings: AppSettings | None = None,
    *,
    profile_path: Path | None = None,
    model_factory: ModelFactory | None = None,
    speech_factory: SpeechFactory | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_profile_path = profile_path or _default_profile_path()
    resolved_model_factory = model_factory or (
        lambda: FakeModelProvider(lambda request: f"I heard you say: {request.user_text}.")
    )
    resolved_speech_factory = speech_factory or FakeSpeechSynthesizer

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        resolved_settings.data_dir.mkdir(parents=True, exist_ok=True)
        database = SqliteDatabase(resolved_settings.database_path)
        store = SqliteStore(database)
        features = await asyncio.to_thread(store.initialize)
        profile = load_character_profile(resolved_profile_path)
        supervisor = SessionSupervisor()
        context_service = CharacterContextService(
            profile,
            resolved_settings.context,
            ContextCompiler(),
            ContextSnapshotCache(),
            store.context_snapshots,
        )
        app.state.resources = AppResources(
            settings=resolved_settings,
            profile=profile,
            store=store,
            supervisor=supervisor,
            context_service=context_service,
            model_factory=resolved_model_factory,
            speech_factory=resolved_speech_factory,
        )
        get_logger(component="server").info(
            "server_started",
            database=str(resolved_settings.database_path),
            sqlite_fts5=features.fts5,
            sqlite_journal_mode=features.journal_mode,
        )
        try:
            yield
        finally:
            await supervisor.shutdown()
            await asyncio.to_thread(store.close)

    app = FastAPI(
        title="Eeveetuber",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def health() -> dict[str, Any]:
        resources = _resources(app)
        return {
            "status": "healthy",
            "version": app.version,
            "character_id": resources.profile.character_id,
            "active_sessions": len(resources.supervisor.active_session_ids),
            "sqlite": {
                "fts5": resources.store.database.features.fts5,
                "journal_mode": resources.store.database.features.journal_mode,
            },
        }

    @app.websocket("/v1/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        resources = _resources(app)
        session = ForegroundSession(
            resources.supervisor,
            resources.context_service,
            resources.store,
            resources.model_factory(),
            resources.speech_factory(),
            inbox_capacity=resources.settings.session_mailbox_capacity,
            outbox_capacity=resources.settings.websocket_send_capacity,
        )
        await session.start()
        sender = asyncio.create_task(
            _send_session_output(websocket, session),
            name=f"websocket-output:{session.session_id}",
        )
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = parse_client_message(raw)
                except ValidationError as error:
                    await websocket.close(code=1003, reason=_validation_reason(error))
                    break
                if isinstance(message, TextTurnMessage):
                    await session.submit_text(message.text)
                elif isinstance(message, CancelTurnMessage):
                    await session.cancel(reason=message.reason)
                elif isinstance(message, PingMessage):
                    await session.ping(message.message_id)
                elif isinstance(message, OperatorControlMessage):
                    if message.action == "neutral_avatar":
                        await session.request_neutral_avatar(message.message_id)
                    elif message.action == "stop_speech":
                        await session.cancel(reason="operator_stop_speech")
                    else:
                        break
        except WebSocketDisconnect:
            pass
        finally:
            await session.stop()
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    return app


async def _send_session_output(websocket: WebSocket, session: ForegroundSession) -> None:
    try:
        while True:
            event = await session.receive_output()
            raw_generation = event.payload.get("generation")
            generation = (
                raw_generation
                if isinstance(raw_generation, int) and not isinstance(raw_generation, bool)
                else session.actor.current_generation.value
            )
            message = event_to_server_message(event, generation=generation)
            await websocket.send_text(message.model_dump_json())
    except (MailboxClosed, WebSocketDisconnect):
        return


def _resources(app: FastAPI) -> AppResources:
    resources = getattr(app.state, "resources", None)
    if not isinstance(resources, AppResources):
        raise RuntimeError("application lifespan has not started")
    return resources


def _validation_reason(error: ValidationError) -> str:
    first = error.errors(include_url=False)[0]
    return f"invalid protocol message: {first['msg']}"[:120]


app = create_app()

