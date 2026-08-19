"""Deterministic memory-admission policy.

This policy is intentionally conservative and synchronous.  A future background
consolidator may propose candidates, but it cannot bypass these decisions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from eeveetuber.memory.models import (
    MemoryCandidate,
    MemoryKind,
    PromotionAction,
    PromotionDecision,
    Sensitivity,
    SourceTrust,
    utc_now,
)


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    version: str = "p0-v1"
    auto_commit_confidence: float = 0.9
    clock: Callable[[], datetime] = utc_now

    def decide(self, candidate: MemoryCandidate) -> PromotionDecision:
        action, reasons = self._classify(candidate)
        return PromotionDecision(
            candidate_id=candidate.candidate_id,
            action=action,
            reason_codes=tuple(reasons),
            policy_version=self.version,
            decided_at=self.clock(),
        )

    def _classify(self, candidate: MemoryCandidate) -> tuple[PromotionAction, list[str]]:
        if candidate.kind in {MemoryKind.POLICY, MemoryKind.SECURITY}:
            return PromotionAction.REJECT, ["policy_and_security_are_not_memory"]
        if candidate.kind is MemoryKind.PROCEDURAL_SKILL:
            return PromotionAction.REJECT, ["skills_require_separate_work_mode_lifecycle"]

        if candidate.provenance.source_trust in {
            SourceTrust.PUBLIC_VIEWER,
            SourceTrust.UNVERIFIED_IMPORT,
        }:
            return PromotionAction.QUARANTINE, ["untrusted_or_public_source"]

        review_reasons: list[str] = []
        if candidate.kind in {MemoryKind.CANON, MemoryKind.PERSONA}:
            review_reasons.append("owner_controlled_identity")
        if candidate.sensitivity in {Sensitivity.PRIVATE, Sensitivity.RESTRICTED}:
            review_reasons.append("sensitive_content")
        if candidate.contradiction_revision_ids:
            review_reasons.append("contradicts_committed_memory")
        if candidate.provenance.source_trust is SourceTrust.MODEL_INFERENCE:
            review_reasons.append("model_inference")
        if not candidate.provenance.consented:
            review_reasons.append("consent_not_recorded")
        if candidate.confidence < self.auto_commit_confidence:
            review_reasons.append("below_auto_commit_confidence")

        auto_kinds = {
            MemoryKind.USER_PROFILE,
            MemoryKind.RELATIONSHIP,
            MemoryKind.SEMANTIC_FACT,
            MemoryKind.EPISODE,
            MemoryKind.ACTIVE_TASK,
        }
        auto_trust = {
            SourceTrust.OWNER_AUTHORED,
            SourceTrust.OWNER_STATED,
            SourceTrust.TRUSTED_OPERATOR,
        }
        if review_reasons or candidate.kind not in auto_kinds:
            return PromotionAction.REQUIRE_REVIEW, review_reasons or ["class_requires_review"]
        if candidate.provenance.source_trust not in auto_trust:
            return PromotionAction.REQUIRE_REVIEW, ["source_requires_review"]
        return PromotionAction.AUTO_COMMIT, ["high_confidence_low_risk_owner_source"]

