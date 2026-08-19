from __future__ import annotations

from datetime import UTC, datetime

from eeveetuber.memory.models import (
    MemoryCandidate,
    MemoryKind,
    MemoryProvenance,
    MemoryScope,
    PromotionAction,
    ScopeKind,
    Sensitivity,
    SourceKind,
    SourceTrust,
    Visibility,
)
from eeveetuber.memory.promotion import PromotionPolicy

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


def candidate(
    *,
    kind: MemoryKind = MemoryKind.USER_PROFILE,
    trust: SourceTrust = SourceTrust.OWNER_STATED,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    confidence: float = 0.98,
    consented: bool = True,
    contradictions: tuple[str, ...] = (),
) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=f"candidate-{kind.value}-{trust.value}",
        namespace="eevee",
        kind=kind,
        scope=MemoryScope(kind=ScopeKind.OWNER, subject_id="owner-1"),
        subject="favorite tea",
        content="The owner prefers jasmine tea.",
        provenance=MemoryProvenance(
            source_kind=SourceKind.OWNER_STATEMENT,
            source_id="message-7",
            source_trust=trust,
            observed_at=NOW,
            actor_id="owner-1",
            evidence_event_ids=("event-7",),
            consented=consented,
        ),
        confidence=confidence,
        sensitivity=sensitivity,
        visibility=Visibility.TRUSTED_CONTEXT,
        created_at=NOW,
        contradiction_revision_ids=contradictions,
    )


def test_low_risk_owner_fact_can_auto_commit() -> None:
    decision = PromotionPolicy(clock=lambda: NOW).decide(candidate())
    assert decision.action is PromotionAction.AUTO_COMMIT


def test_public_source_is_quarantined_even_at_high_confidence() -> None:
    decision = PromotionPolicy(clock=lambda: NOW).decide(
        candidate(trust=SourceTrust.PUBLIC_VIEWER)
    )
    assert decision.action is PromotionAction.QUARANTINE
    assert "untrusted_or_public_source" in decision.reason_codes


def test_sensitive_or_contradictory_fact_requires_review() -> None:
    sensitive = PromotionPolicy(clock=lambda: NOW).decide(
        candidate(sensitivity=Sensitivity.RESTRICTED)
    )
    contradictory = PromotionPolicy(clock=lambda: NOW).decide(
        candidate(contradictions=("revision-old",))
    )
    assert sensitive.action is PromotionAction.REQUIRE_REVIEW
    assert contradictory.action is PromotionAction.REQUIRE_REVIEW


def test_canon_requires_review_and_policy_is_rejected() -> None:
    canon = PromotionPolicy(clock=lambda: NOW).decide(candidate(kind=MemoryKind.CANON))
    policy = PromotionPolicy(clock=lambda: NOW).decide(candidate(kind=MemoryKind.POLICY))
    assert canon.action is PromotionAction.REQUIRE_REVIEW
    assert policy.action is PromotionAction.REJECT

