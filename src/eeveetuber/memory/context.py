"""Deterministic, revision-pinned context compilation for the realtime lane.

The compiler accepts only already-available local values.  It has no hook for a
model, network retriever, reflection process, or skill discovery service.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Protocol

from pydantic import Field, model_validator

from eeveetuber.memory.models import FrozenModel, utc_now


class ContextTier(StrEnum):
    T0_CANON = "t0_canon"
    T1_HOT = "t1_hot"
    T2_MAP = "t2_map"


class ContextClass(StrEnum):
    CANON = "canon"
    PERSONA = "persona"
    OWNER_PROFILE = "owner_profile"
    RELATIONSHIP = "relationship"
    SESSION_STATE = "session_state"
    MEMORY_DIRECTORY = "memory_directory"


class TokenEstimator(Protocol):
    def estimate(self, text: str) -> int: ...

    def truncate(self, text: str, max_tokens: int) -> str: ...


class Utf8HeuristicTokenEstimator:
    """Dependency-free conservative heuristic, replaceable by provider tokenizer.

    Four UTF-8 bytes per token is only a budget estimate.  The same estimator is
    used for truncation and accounting, making the hard local budget deterministic.
    """

    bytes_per_token = 4

    def estimate(self, text: str) -> int:
        if not text:
            return 0
        byte_count = len(text.encode("utf-8"))
        return (byte_count + self.bytes_per_token - 1) // self.bytes_per_token

    def truncate(self, text: str, max_tokens: int) -> str:
        if max_tokens <= 0:
            return ""
        if self.estimate(text) <= max_tokens:
            return text
        byte_limit = max_tokens * self.bytes_per_token
        encoded = text.encode("utf-8")[:byte_limit]
        while encoded:
            try:
                result = encoded.decode("utf-8")
                break
            except UnicodeDecodeError:
                encoded = encoded[:-1]
        else:
            return ""
        while result and self.estimate(result) > max_tokens:
            result = result[:-1]
        return result.rstrip()


class ContextBudget(FrozenModel):
    t0_tokens: int = Field(default=512, ge=1)
    t1_tokens: int = Field(default=768, ge=0)
    t2_tokens: int = Field(default=384, ge=0)
    total_tokens: int = Field(default=1664, ge=1)


class ContextRevisionPin(FrozenModel):
    namespace: str = Field(min_length=1, max_length=256)
    memory_generation: int = Field(ge=0)
    canon_revision: str = Field(min_length=1, max_length=128)
    persona_revision: str | None = Field(default=None, max_length=128)
    profile_revision: str | None = Field(default=None, max_length=128)
    relationship_revision: str | None = Field(default=None, max_length=128)
    session_revision: str | None = Field(default=None, max_length=128)
    recipe_revision: str = Field(default="default-v1", min_length=1, max_length=128)


class ContextEntry(FrozenModel):
    entry_id: str = Field(min_length=1, max_length=256)
    tier: ContextTier
    context_class: ContextClass
    text: str = Field(min_length=1)
    priority: int = Field(default=0, ge=-10_000, le=10_000)
    mandatory: bool = False
    demotable: bool = True
    source_revision_id: str | None = Field(default=None, max_length=128)
    source_memory_id: str | None = Field(default=None, max_length=128)


class CompiledContextEntry(FrozenModel):
    entry_id: str
    original_tier: ContextTier
    tier: ContextTier
    context_class: ContextClass
    text: str
    estimated_tokens: int = Field(ge=0)
    priority: int
    mandatory: bool
    source_revision_id: str | None = None
    source_memory_id: str | None = None


class ContextDemotion(FrozenModel):
    entry_id: str
    from_tier: ContextTier
    to_tier: ContextTier
    reason: str = "tier_budget_exceeded"


class ContextTrim(FrozenModel):
    entry_id: str
    tier: ContextTier
    original_tokens: int
    retained_tokens: int
    reason: str


class ContextDrop(FrozenModel):
    entry_id: str
    tier: ContextTier
    reason: str


class ContextTokenUsage(FrozenModel):
    t0_tokens: int = Field(ge=0)
    t1_tokens: int = Field(ge=0)
    t2_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class ContextCompileRequest(FrozenModel):
    session_id: str = Field(min_length=1, max_length=128)
    turn_id: str = Field(min_length=1, max_length=128)
    revision: ContextRevisionPin
    budget: ContextBudget = ContextBudget()
    entries: tuple[ContextEntry, ...] = ()
    activity_mode: str = Field(default="conversation", min_length=1, max_length=64)
    privacy_recipe: str = Field(default="private", min_length=1, max_length=64)
    minimal_canon: str = Field(
        default="Remain in character and follow the current owner's explicit intent.",
        min_length=1,
    )

    @model_validator(mode="after")
    def unique_entries(self) -> ContextCompileRequest:
        ids = [entry.entry_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("context entry IDs must be unique")
        return self


class ContextSnapshot(FrozenModel):
    snapshot_id: str = Field(min_length=1, max_length=128)
    session_id: str
    turn_id: str
    revision: ContextRevisionPin
    activity_mode: str
    privacy_recipe: str
    budget: ContextBudget
    entries: tuple[CompiledContextEntry, ...]
    usage: ContextTokenUsage
    demotions: tuple[ContextDemotion, ...] = ()
    trims: tuple[ContextTrim, ...] = ()
    drops: tuple[ContextDrop, ...] = ()
    used_minimal_fallback: bool = False
    rendered_context: str
    created_at: datetime


_TIER_ORDER = (ContextTier.T0_CANON, ContextTier.T1_HOT, ContextTier.T2_MAP)
_NEXT_TIER = {
    ContextTier.T0_CANON: ContextTier.T1_HOT,
    ContextTier.T1_HOT: ContextTier.T2_MAP,
}


class ContextCompiler:
    """Compile a bounded snapshot using deterministic priority and stable IDs."""

    def __init__(
        self,
        *,
        estimator: TokenEstimator | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._estimator = estimator or Utf8HeuristicTokenEstimator()
        self._id_factory = id_factory or _new_snapshot_id
        self._clock = clock

    def compile(self, request: ContextCompileRequest) -> ContextSnapshot:
        queues: dict[ContextTier, list[tuple[ContextEntry, ContextTier]]] = {
            tier: [] for tier in _TIER_ORDER
        }
        has_mandatory_t0 = any(
            entry.tier is ContextTier.T0_CANON and entry.mandatory
            for entry in request.entries
        )
        used_fallback = not has_mandatory_t0
        entries: Iterable[ContextEntry] = request.entries
        if used_fallback:
            fallback = ContextEntry(
                entry_id="__minimal_canon__",
                tier=ContextTier.T0_CANON,
                context_class=ContextClass.CANON,
                text=request.minimal_canon,
                priority=10_000,
                mandatory=True,
                demotable=False,
                source_revision_id=request.revision.canon_revision,
            )
            entries = (*request.entries, fallback)
        for entry in entries:
            queues[entry.tier].append((entry, entry.tier))

        selected: list[CompiledContextEntry] = []
        demotions: list[ContextDemotion] = []
        trims: list[ContextTrim] = []
        drops: list[ContextDrop] = []
        used_by_tier = {tier: 0 for tier in _TIER_ORDER}
        total_used = 0

        configured = {
            ContextTier.T0_CANON: request.budget.t0_tokens,
            ContextTier.T1_HOT: request.budget.t1_tokens,
            ContextTier.T2_MAP: request.budget.t2_tokens,
        }
        for tier in _TIER_ORDER:
            queue = sorted(queues[tier], key=lambda item: _entry_sort_key(item[0]))
            tier_allowance = min(configured[tier], request.budget.total_tokens - total_used)
            for entry, original_tier in queue:
                tokens = self._estimator.estimate(entry.text)
                remaining = max(0, tier_allowance - used_by_tier[tier])
                if tokens <= remaining:
                    selected.append(_compiled(entry, original_tier, tier, tokens))
                    used_by_tier[tier] += tokens
                    total_used += tokens
                    continue

                if entry.mandatory and remaining > 0:
                    retained = self._estimator.truncate(entry.text, remaining)
                    retained_tokens = self._estimator.estimate(retained)
                    if retained:
                        selected.append(
                            _compiled(entry, original_tier, tier, retained_tokens, text=retained)
                        )
                        used_by_tier[tier] += retained_tokens
                        total_used += retained_tokens
                        trims.append(
                            ContextTrim(
                                entry_id=entry.entry_id,
                                tier=tier,
                                original_tokens=tokens,
                                retained_tokens=retained_tokens,
                                reason="mandatory_entry_truncated_to_budget",
                            )
                        )
                        continue

                next_tier = _NEXT_TIER.get(tier)
                if entry.demotable and not entry.mandatory and next_tier is not None:
                    queues[next_tier].append((entry, original_tier))
                    demotions.append(
                        ContextDemotion(
                            entry_id=entry.entry_id,
                            from_tier=tier,
                            to_tier=next_tier,
                        )
                    )
                else:
                    drops.append(
                        ContextDrop(
                            entry_id=entry.entry_id,
                            tier=tier,
                            reason="total_or_tier_budget_exceeded",
                        )
                    )

        selected.sort(key=lambda entry: (_TIER_ORDER.index(entry.tier), -entry.priority, entry.entry_id))
        usage = ContextTokenUsage(
            t0_tokens=used_by_tier[ContextTier.T0_CANON],
            t1_tokens=used_by_tier[ContextTier.T1_HOT],
            t2_tokens=used_by_tier[ContextTier.T2_MAP],
            total_tokens=total_used,
        )
        rendered = _render_context(request.revision, selected)
        return ContextSnapshot(
            snapshot_id=self._id_factory(),
            session_id=request.session_id,
            turn_id=request.turn_id,
            revision=request.revision,
            activity_mode=request.activity_mode,
            privacy_recipe=request.privacy_recipe,
            budget=request.budget,
            entries=tuple(selected),
            usage=usage,
            demotions=tuple(demotions),
            trims=tuple(trims),
            drops=tuple(drops),
            used_minimal_fallback=used_fallback,
            rendered_context=rendered,
            created_at=self._clock(),
        )


class StaleSnapshotError(ValueError):
    """Raised when an older memory generation is published over a newer one."""


class ContextSnapshotCache:
    """Small atomic latest-snapshot cache; returned values are immutable pins."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._latest: dict[str, ContextSnapshot] = {}
        self._by_id: dict[str, ContextSnapshot] = {}

    def publish(self, snapshot: ContextSnapshot) -> None:
        namespace = snapshot.revision.namespace
        with self._lock:
            current = self._latest.get(namespace)
            if current and snapshot.revision.memory_generation < current.revision.memory_generation:
                raise StaleSnapshotError(
                    f"generation {snapshot.revision.memory_generation} is older than "
                    f"published generation {current.revision.memory_generation}"
                )
            self._latest[namespace] = snapshot
            self._by_id[snapshot.snapshot_id] = snapshot

    def latest(self, namespace: str) -> ContextSnapshot | None:
        with self._lock:
            return self._latest.get(namespace)

    def get(self, snapshot_id: str) -> ContextSnapshot | None:
        with self._lock:
            return self._by_id.get(snapshot_id)


def _entry_sort_key(entry: ContextEntry) -> tuple[int, int, str]:
    return (-int(entry.mandatory), -entry.priority, entry.entry_id)


def _compiled(
    entry: ContextEntry,
    original_tier: ContextTier,
    tier: ContextTier,
    tokens: int,
    *,
    text: str | None = None,
) -> CompiledContextEntry:
    return CompiledContextEntry(
        entry_id=entry.entry_id,
        original_tier=original_tier,
        tier=tier,
        context_class=entry.context_class,
        text=text if text is not None else entry.text,
        estimated_tokens=tokens,
        priority=entry.priority,
        mandatory=entry.mandatory,
        source_revision_id=entry.source_revision_id,
        source_memory_id=entry.source_memory_id,
    )


def _render_context(
    revision: ContextRevisionPin,
    entries: list[CompiledContextEntry],
) -> str:
    grouped = {
        tier: [
            {
                "id": item.entry_id,
                "class": item.context_class.value,
                "text": item.text,
                "revision_id": item.source_revision_id,
                "memory_id": item.source_memory_id,
            }
            for item in entries
            if item.tier is tier
        ]
        for tier in _TIER_ORDER
    }
    lines = [
        f'<context_snapshot namespace="{revision.namespace}" generation="{revision.memory_generation}">',
        "<owner_canon instruction_authority=\"true\">",
        json.dumps(grouped[ContextTier.T0_CANON], ensure_ascii=False, separators=(",", ":")),
        "</owner_canon>",
        '<personal_context instruction_authority="false" data_only="true">',
        json.dumps(grouped[ContextTier.T1_HOT], ensure_ascii=False, separators=(",", ":")),
        "</personal_context>",
        '<memory_directory instruction_authority="false" untrusted_data="true">',
        json.dumps(grouped[ContextTier.T2_MAP], ensure_ascii=False, separators=(",", ":")),
        "</memory_directory>",
        "</context_snapshot>",
    ]
    return "\n".join(lines)


def _new_snapshot_id() -> str:
    from uuid import uuid4

    return f"ctx_{uuid4().hex}"

