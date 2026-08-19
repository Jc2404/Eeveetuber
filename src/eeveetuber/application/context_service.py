"""Compile and persist a deterministic P0 context snapshot for each turn."""

from __future__ import annotations

import asyncio
from uuid import UUID

from eeveetuber.config import CharacterProfile, ContextBudgetSettings
from eeveetuber.memory.context import (
    ContextBudget,
    ContextClass,
    ContextCompiler,
    ContextCompileRequest,
    ContextEntry,
    ContextRevisionPin,
    ContextSnapshot,
    ContextSnapshotCache,
    ContextTier,
)
from eeveetuber.storage.context_repository import SqliteContextSnapshotRepository


class CharacterContextService:
    """Own the current immutable character context generation.

    This initial service has owner-authored canon/persona only. Learned T1 state and the T2 memory
    directory will enter through committed repository revisions in Phase 2 without changing callers.
    """

    def __init__(
        self,
        profile: CharacterProfile,
        budgets: ContextBudgetSettings,
        compiler: ContextCompiler,
        cache: ContextSnapshotCache,
        repository: SqliteContextSnapshotRepository,
    ) -> None:
        self._profile = profile
        self._compiler = compiler
        self._cache = cache
        self._repository = repository
        self._budget = ContextBudget(
            t0_tokens=budgets.t0_canon_tokens,
            t1_tokens=budgets.t1_hot_tokens,
            t2_tokens=budgets.t2_map_tokens,
            total_tokens=budgets.total_tokens,
        )
        self._memory_generation = 0

    @property
    def namespace(self) -> str:
        return f"character:{self._profile.character_id}"

    def compile_for_turn(self, session_id: UUID, turn_id: UUID) -> ContextSnapshot:
        revision = ContextRevisionPin(
            namespace=self.namespace,
            memory_generation=self._memory_generation,
            canon_revision=self._profile.revision,
            persona_revision=self._profile.revision,
        )
        request = ContextCompileRequest(
            session_id=str(session_id),
            turn_id=str(turn_id),
            revision=revision,
            budget=self._budget,
            entries=(
                ContextEntry(
                    entry_id="owner-canon",
                    tier=ContextTier.T0_CANON,
                    context_class=ContextClass.CANON,
                    text=self._profile.context.canon,
                    priority=10_000,
                    mandatory=True,
                    demotable=False,
                    source_revision_id=self._profile.revision,
                ),
                ContextEntry(
                    entry_id="character-persona",
                    tier=ContextTier.T1_HOT,
                    context_class=ContextClass.PERSONA,
                    text=self._profile.context.persona,
                    priority=1_000,
                    source_revision_id=self._profile.revision,
                ),
            ),
        )
        snapshot = self._compiler.compile(request)
        self._cache.publish(snapshot)
        return snapshot

    async def persist_snapshot(self, snapshot: ContextSnapshot) -> None:
        """Persist off the first-audio dependency path."""

        await asyncio.to_thread(self._repository.save, snapshot)
