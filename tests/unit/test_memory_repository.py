from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from eeveetuber.memory.models import (
    MemoryCandidate,
    MemoryKind,
    MemoryProvenance,
    MemoryScope,
    ScopeKind,
    Sensitivity,
    SourceKind,
    SourceTrust,
    Visibility,
)
from eeveetuber.memory.promotion import PromotionPolicy
from eeveetuber.storage import OptimisticConcurrencyError, SqliteDatabase, SqliteStore

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path) -> SqliteStore:
    value = SqliteStore(SqliteDatabase(tmp_path / "memory.db"))
    value.initialize()
    yield value
    value.close()


def make_candidate(
    candidate_id: str,
    content: str,
    *,
    base_memory_id: str | None = None,
    base_revision_id: str | None = None,
    visibility: Visibility = Visibility.STREAM_SAFE,
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=candidate_id,
        namespace="eevee",
        kind=MemoryKind.USER_PROFILE,
        scope=MemoryScope(kind=ScopeKind.OWNER, subject_id="owner-1"),
        subject="favorite tea",
        content=content,
        provenance=MemoryProvenance(
            source_kind=SourceKind.OWNER_STATEMENT,
            source_id=f"message-{candidate_id}",
            source_trust=SourceTrust.OWNER_STATED,
            observed_at=NOW,
            actor_id="owner-1",
            evidence_event_ids=(f"event-{candidate_id}",),
            consented=True,
        ),
        confidence=0.99,
        sensitivity=Sensitivity.INTERNAL,
        visibility=visibility,
        created_at=NOW,
        base_memory_id=base_memory_id,
        base_revision_id=base_revision_id,
    )


def test_candidate_promotion_creates_revision_and_generation(store: SqliteStore) -> None:
    policy = PromotionPolicy(clock=lambda: NOW)
    first = make_candidate("candidate-1", "The owner likes jasmine tea.")
    store.memories.add_candidate(first)
    result = store.memories.apply_decision(policy.decide(first))

    assert result.revision is not None
    assert result.generation == 1
    assert result.revision.promoted_from_candidate_id == first.candidate_id
    assert store.memories.current_generation("eevee") == 1
    record = store.memories.get_record(result.revision.memory_id)
    assert record is not None
    assert record.current_revision == result.revision

    second = make_candidate(
        "candidate-2",
        "The owner now prefers roasted jasmine tea.",
        base_memory_id=result.revision.memory_id,
        base_revision_id=result.revision.revision_id,
    )
    store.memories.add_candidate(second)
    update = store.memories.apply_decision(policy.decide(second))
    assert update.revision is not None
    assert update.generation == 2
    assert update.revision.parent_revision_id == result.revision.revision_id
    assert store.memories.get_record(result.revision.memory_id).current_revision == update.revision  # type: ignore[union-attr]


def test_stale_base_revision_is_rejected(store: SqliteStore) -> None:
    policy = PromotionPolicy(clock=lambda: NOW)
    original = make_candidate("candidate-1", "The owner likes jasmine tea.")
    store.memories.add_candidate(original)
    original_result = store.memories.apply_decision(policy.decide(original))
    assert original_result.revision is not None

    current = make_candidate(
        "candidate-2",
        "The owner prefers roasted jasmine tea.",
        base_memory_id=original_result.revision.memory_id,
        base_revision_id=original_result.revision.revision_id,
    )
    stale = make_candidate(
        "candidate-stale",
        "The owner dislikes all tea.",
        base_memory_id=original_result.revision.memory_id,
        base_revision_id=original_result.revision.revision_id,
    )
    store.memories.add_candidate(current)
    store.memories.add_candidate(stale)
    store.memories.apply_decision(policy.decide(current))

    with pytest.raises(OptimisticConcurrencyError):
        store.memories.apply_decision(policy.decide(stale))
    assert store.memories.current_generation("eevee") == 2


def test_memory_fts_is_bounded_and_visibility_filtered(store: SqliteStore) -> None:
    if not store.database.features.fts5:
        pytest.skip("SQLite build has no FTS5")
    policy = PromotionPolicy(clock=lambda: NOW)
    public = make_candidate("candidate-public", "The owner likes jasmine tea.")
    private = make_candidate(
        "candidate-private",
        "The owner's secret jasmine supplier.",
        visibility=Visibility.OWNER_ONLY,
    )
    for value in (public, private):
        store.memories.add_candidate(value)
        store.memories.apply_decision(policy.decide(value))

    stream_hits = store.memories.search("eevee", "jasmine", limit=10)
    assert [hit.content for hit in stream_hits] == [public.content]
    owner_hits = store.memories.search(
        "eevee",
        "jasmine",
        limit=10,
        allowed_visibilities=frozenset({Visibility.STREAM_SAFE, Visibility.OWNER_ONLY}),
    )
    assert {hit.content for hit in owner_hits} == {public.content, private.content}
