"""Typed, immutable personal-memory domain models.

These types deliberately contain no model-provider or database dependencies.  The
realtime context path can therefore reason about committed data without invoking
an auxiliary model or importing the persistence implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(UTC)


class FrozenModel(BaseModel):
    """Base class for value objects that must not change after publication."""

    model_config = ConfigDict(frozen=True, extra="forbid", use_enum_values=False)


class MemoryKind(StrEnum):
    CANON = "canon"
    PERSONA = "persona"
    USER_PROFILE = "user_profile"
    VIEWER_PROFILE = "viewer_profile"
    RELATIONSHIP = "relationship"
    SEMANTIC_FACT = "semantic_fact"
    EPISODE = "episode"
    ACTIVE_TASK = "active_task"
    PROCEDURAL_SKILL = "procedural_skill"
    POLICY = "policy"
    SECURITY = "security"


class ScopeKind(StrEnum):
    CHARACTER = "character"
    OWNER = "owner"
    USER = "user"
    VIEWER = "viewer"
    RELATIONSHIP = "relationship"
    CHANNEL = "channel"
    SESSION = "session"
    GLOBAL = "global"


class SourceKind(StrEnum):
    OWNER_STATEMENT = "owner_statement"
    OWNER_EDIT = "owner_edit"
    TRUSTED_OPERATOR = "trusted_operator"
    PRIVATE_CONVERSATION = "private_conversation"
    PUBLIC_CHAT = "public_chat"
    TOOL_OBSERVATION = "tool_observation"
    IMPORT = "import"
    MODEL_INFERENCE = "model_inference"


class SourceTrust(StrEnum):
    OWNER_AUTHORED = "owner_authored"
    OWNER_STATED = "owner_stated"
    TRUSTED_OPERATOR = "trusted_operator"
    AUTHENTICATED_USER = "authenticated_user"
    TOOL_OBSERVATION = "tool_observation"
    MODEL_INFERENCE = "model_inference"
    PUBLIC_VIEWER = "public_viewer"
    UNVERIFIED_IMPORT = "unverified_import"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    PRIVATE = "private"
    RESTRICTED = "restricted"


class Visibility(StrEnum):
    STREAM_SAFE = "stream_safe"
    TRUSTED_CONTEXT = "trusted_context"
    PRIVATE_SESSION = "private_session"
    OWNER_ONLY = "owner_only"


class CandidateStatus(StrEnum):
    PENDING = "pending"
    REVIEW_REQUIRED = "review_required"
    QUARANTINED = "quarantined"
    REJECTED = "rejected"
    COMMITTED = "committed"


class RevisionStatus(StrEnum):
    COMMITTED = "committed"
    TOMBSTONED = "tombstoned"


class PromotionAction(StrEnum):
    AUTO_COMMIT = "auto_commit"
    REQUIRE_REVIEW = "require_review"
    QUARANTINE = "quarantine"
    REJECT = "reject"


class MemoryScope(FrozenModel):
    kind: ScopeKind
    subject_id: str = Field(min_length=1, max_length=256)


class MemoryProvenance(FrozenModel):
    source_kind: SourceKind
    source_id: str = Field(min_length=1, max_length=512)
    source_trust: SourceTrust
    observed_at: datetime
    actor_id: str | None = Field(default=None, max_length=256)
    evidence_event_ids: tuple[str, ...] = ()
    consented: bool = False


class MemoryCandidate(FrozenModel):
    """A source-labelled proposal; it is never prompt-visible by default."""

    candidate_id: str = Field(min_length=1, max_length=128)
    namespace: str = Field(min_length=1, max_length=256)
    kind: MemoryKind
    scope: MemoryScope
    subject: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1)
    provenance: MemoryProvenance
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: Sensitivity
    visibility: Visibility
    created_at: datetime
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    ttl_seconds: int | None = Field(default=None, ge=1)
    base_memory_id: str | None = Field(default=None, max_length=128)
    base_revision_id: str | None = Field(default=None, max_length=128)
    contradiction_revision_ids: tuple[str, ...] = ()
    status: CandidateStatus = CandidateStatus.PENDING

    @model_validator(mode="after")
    def validate_interval_and_base(self) -> MemoryCandidate:
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        if self.base_revision_id and not self.base_memory_id:
            raise ValueError("base_revision_id requires base_memory_id")
        return self


class MemoryRevision(FrozenModel):
    revision_id: str = Field(min_length=1, max_length=128)
    memory_id: str = Field(min_length=1, max_length=128)
    namespace: str = Field(min_length=1, max_length=256)
    generation: int = Field(ge=1)
    parent_revision_id: str | None = Field(default=None, max_length=128)
    promoted_from_candidate_id: str | None = Field(default=None, max_length=128)
    kind: MemoryKind
    scope: MemoryScope
    subject: str = Field(min_length=1, max_length=512)
    content: str = Field(min_length=1)
    provenance: MemoryProvenance
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: Sensitivity
    visibility: Visibility
    created_at: datetime
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    ttl_seconds: int | None = Field(default=None, ge=1)
    status: RevisionStatus = RevisionStatus.COMMITTED


class MemoryRecord(FrozenModel):
    memory_id: str
    namespace: str
    kind: MemoryKind
    scope: MemoryScope
    subject: str
    created_at: datetime
    current_revision: MemoryRevision
    deleted_at: datetime | None = None


class PromotionDecision(FrozenModel):
    candidate_id: str = Field(min_length=1, max_length=128)
    action: PromotionAction
    reason_codes: tuple[str, ...] = Field(min_length=1)
    policy_version: str = Field(min_length=1, max_length=64)
    decided_at: datetime
    decided_by: str = Field(default="memory-policy", min_length=1, max_length=256)


class PromotionResult(FrozenModel):
    decision: PromotionDecision
    candidate_status: CandidateStatus
    generation: int
    revision: MemoryRevision | None = None


class MemorySearchHit(FrozenModel):
    memory_id: str
    revision_id: str
    namespace: str
    subject: str
    content: str
    score: float
    visibility: Visibility
    sensitivity: Sensitivity
