from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from eeveetuber.memory.context import (
    ContextBudget,
    ContextClass,
    ContextCompiler,
    ContextCompileRequest,
    ContextEntry,
    ContextRevisionPin,
    ContextSnapshotCache,
    ContextTier,
    StaleSnapshotError,
)


class WordEstimator:
    def estimate(self, text: str) -> int:
        return len(text.split())

    def truncate(self, text: str, max_tokens: int) -> str:
        return " ".join(text.split()[:max_tokens])


NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


def revision(generation: int = 4) -> ContextRevisionPin:
    return ContextRevisionPin(
        namespace="eevee",
        memory_generation=generation,
        canon_revision="canon-2",
        persona_revision="persona-7",
        profile_revision="profile-3",
        relationship_revision="relationship-9",
        session_revision="session-11",
        recipe_revision="private-v2",
    )


def compiler(snapshot_id: str = "ctx-test") -> ContextCompiler:
    return ContextCompiler(
        estimator=WordEstimator(),
        id_factory=lambda: snapshot_id,
        clock=lambda: NOW,
    )


def test_compiler_pins_revision_and_deterministically_demotes() -> None:
    request = ContextCompileRequest(
        session_id="session-1",
        turn_id="turn-1",
        revision=revision(),
        budget=ContextBudget(t0_tokens=3, t1_tokens=2, t2_tokens=3, total_tokens=8),
        entries=(
            ContextEntry(
                entry_id="canon",
                tier=ContextTier.T0_CANON,
                context_class=ContextClass.CANON,
                text="kind curious",
                priority=100,
                mandatory=True,
                demotable=False,
                source_revision_id="canon-2",
            ),
            ContextEntry(
                entry_id="style",
                tier=ContextTier.T0_CANON,
                context_class=ContextClass.PERSONA,
                text="warm concise playful",
                priority=10,
            ),
            ContextEntry(
                entry_id="relationship",
                tier=ContextTier.T1_HOT,
                context_class=ContextClass.RELATIONSHIP,
                text="old friend",
                priority=20,
            ),
        ),
    )

    first = compiler().compile(request)
    second = compiler().compile(request)

    assert first == second
    assert first.revision == revision()
    assert first.usage.total_tokens == 7
    assert first.usage.t0_tokens == 2
    assert first.usage.t1_tokens == 2
    assert first.usage.t2_tokens == 3
    assert [(item.entry_id, item.tier) for item in first.entries] == [
        ("canon", ContextTier.T0_CANON),
        ("relationship", ContextTier.T1_HOT),
        ("style", ContextTier.T2_MAP),
    ]
    assert [(item.from_tier, item.to_tier) for item in first.demotions] == [
        (ContextTier.T0_CANON, ContextTier.T1_HOT),
        (ContextTier.T1_HOT, ContextTier.T2_MAP),
    ]
    assert 'instruction_authority="false"' in first.rendered_context


def test_minimal_fallback_is_valid_and_hard_bounded() -> None:
    snapshot = compiler().compile(
        ContextCompileRequest(
            session_id="session-1",
            turn_id="turn-empty",
            revision=revision(),
            budget=ContextBudget(t0_tokens=2, t1_tokens=0, t2_tokens=0, total_tokens=2),
            minimal_canon="stay kind and responsive always",
        )
    )

    assert snapshot.used_minimal_fallback is True
    assert snapshot.usage.total_tokens == 2
    assert snapshot.entries[0].entry_id == "__minimal_canon__"
    assert snapshot.entries[0].text == "stay kind"
    assert snapshot.trims[0].reason == "mandatory_entry_truncated_to_budget"


def test_snapshot_and_revision_pin_are_immutable() -> None:
    snapshot = compiler().compile(
        ContextCompileRequest(session_id="s", turn_id="t", revision=revision())
    )

    with pytest.raises(ValidationError):
        snapshot.turn_id = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        snapshot.revision.memory_generation = 99  # type: ignore[misc]


def test_cache_returns_same_pin_and_rejects_generation_regression() -> None:
    cache = ContextSnapshotCache()
    newest = compiler("ctx-new").compile(
        ContextCompileRequest(session_id="s", turn_id="t-2", revision=revision(5))
    )
    old = compiler("ctx-old").compile(
        ContextCompileRequest(session_id="s", turn_id="t-1", revision=revision(4))
    )

    cache.publish(newest)
    assert cache.latest("eevee") is newest
    assert cache.get("ctx-new") is newest
    with pytest.raises(StaleSnapshotError):
        cache.publish(old)

