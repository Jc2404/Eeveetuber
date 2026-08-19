"""Persistence ports consumed by memory and runtime services."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from eeveetuber.memory.context import ContextSnapshot
from eeveetuber.memory.models import (
    MemoryCandidate,
    MemoryRecord,
    MemorySearchHit,
    PromotionDecision,
    PromotionResult,
    Visibility,
)


class MemoryRepository(Protocol):
    def current_generation(self, namespace: str) -> int: ...

    def add_candidate(self, candidate: MemoryCandidate) -> MemoryCandidate: ...

    def get_record(self, memory_id: str) -> MemoryRecord | None: ...

    def apply_decision(
        self,
        decision: PromotionDecision,
        *,
        approved_by: str | None = None,
    ) -> PromotionResult: ...

    def search(
        self,
        namespace: str,
        query: str,
        *,
        limit: int = 5,
        allowed_visibilities: frozenset[Visibility] = frozenset({Visibility.STREAM_SAFE}),
    ) -> Sequence[MemorySearchHit]: ...


class ContextSnapshotRepository(Protocol):
    def save(self, snapshot: ContextSnapshot) -> ContextSnapshot: ...

    def get(self, snapshot_id: str) -> ContextSnapshot | None: ...

    def latest_for_namespace(self, namespace: str) -> ContextSnapshot | None: ...
