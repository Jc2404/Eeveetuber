"""Deadline-bound conversation reads and safely drained off-path writes."""

from __future__ import annotations

import asyncio
from typing import Any

from eeveetuber.application.context_service import CharacterContextService
from eeveetuber.application.conversation_history import (
    RecentConversationHistory,
    RecentConversationHistoryCompiler,
    RecentConversationHistoryPolicy,
)
from eeveetuber.memory.context import ContextSnapshot
from eeveetuber.runtime import SessionActorContext
from eeveetuber.storage import MessageRecord, SqliteStore


class ConversationStorageCoordinator:
    """Own storage workers so foreground cancellation cannot abandon SQLite access."""

    def __init__(
        self,
        store: SqliteStore,
        context_service: CharacterContextService,
        history_policy: RecentConversationHistoryPolicy,
    ) -> None:
        self._store = store
        self._context_service = context_service
        self._history_policy = history_policy
        self._history_compiler = RecentConversationHistoryCompiler(history_policy)
        self._message_writes: dict[int, asyncio.Task[MessageRecord]] = {}
        self._registration_lock = asyncio.Lock()
        self._workers: set[asyncio.Task[Any]] = set()

    @property
    def registration_lock(self) -> asyncio.Lock:
        """Serialize assistant registration against the next history snapshot."""

        return self._registration_lock

    async def load_recent_history(
        self,
        *,
        session_id: str,
        before_sequence: int,
        before_generation: int,
    ) -> RecentConversationHistory:
        policy = self._history_policy
        if not policy.enabled:
            return RecentConversationHistory()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + policy.load_timeout_ms / 1_000
        async with self._registration_lock:
            prior_writes = tuple(
                task
                for sequence, task in self._message_writes.items()
                if sequence < before_sequence and not task.done()
            )
        if prior_writes:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return RecentConversationHistory()
            _done, pending = await asyncio.wait(prior_writes, timeout=remaining)
            if pending:
                return RecentConversationHistory()
        remaining = deadline - loop.time()
        if remaining <= 0:
            return RecentConversationHistory()
        try:
            read_task = asyncio.create_task(
                asyncio.to_thread(
                    self._store.messages.list_recent_before,
                    session_id,
                    before_sequence=before_sequence,
                    limit=policy.fetch_limit,
                ),
                name=f"history-read:{session_id}:{before_sequence}",
            )
            self._track_worker(read_task)
            records = await asyncio.wait_for(asyncio.shield(read_task), timeout=remaining)
        except TimeoutError:
            return RecentConversationHistory()
        return self._history_compiler.compile(
            tuple(records),
            session_id=session_id,
            before_generation=before_generation,
        )

    def persist_message(
        self,
        context: SessionActorContext,
        record: MessageRecord,
        *,
        name: str,
    ) -> None:
        worker = asyncio.create_task(
            asyncio.to_thread(self._store.messages.append, record),
            name=f"{name}:worker",
        )
        self._track_worker(worker)
        context.spawn(self._await_worker(worker), name=name)
        self._message_writes[record.sequence] = worker

        def discard(completed: asyncio.Task[MessageRecord]) -> None:
            if self._message_writes.get(record.sequence) is completed:
                del self._message_writes[record.sequence]

        worker.add_done_callback(discard)

    def persist_snapshot(
        self,
        context: SessionActorContext,
        snapshot: ContextSnapshot,
        *,
        name: str,
    ) -> None:
        worker = asyncio.create_task(
            self._context_service.persist_snapshot(snapshot),
            name=f"{name}:worker",
        )
        self._track_worker(worker)
        context.spawn(self._await_worker(worker), name=name)

    async def drain(self) -> None:
        """Wait for timed-out reads and shielded writes before the store closes."""

        while self._workers:
            workers = tuple(self._workers)
            await asyncio.gather(*workers, return_exceptions=True)

    def _track_worker(self, task: asyncio.Task[Any]) -> None:
        self._workers.add(task)

        def discard(completed: asyncio.Task[Any]) -> None:
            self._workers.discard(completed)
            if not completed.cancelled():
                completed.exception()

        task.add_done_callback(discard)

    @staticmethod
    async def _await_worker[ResultT](worker: asyncio.Task[ResultT]) -> None:
        await asyncio.shield(worker)


__all__ = ["ConversationStorageCoordinator"]
