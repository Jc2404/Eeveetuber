"""Transactional SQLite implementation of personal-memory repositories."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from eeveetuber.memory.models import (
    CandidateStatus,
    MemoryCandidate,
    MemoryKind,
    MemoryProvenance,
    MemoryRecord,
    MemoryRevision,
    MemoryScope,
    MemorySearchHit,
    PromotionAction,
    PromotionDecision,
    PromotionResult,
    RevisionStatus,
    ScopeKind,
    Sensitivity,
    SourceKind,
    SourceTrust,
    Visibility,
)
from eeveetuber.memory.promotion import PromotionPolicy
from eeveetuber.storage._serialization import (
    decode_datetime,
    decode_optional_datetime,
    encode_datetime,
    encode_optional_datetime,
)
from eeveetuber.storage.errors import (
    InvalidPromotion,
    OptimisticConcurrencyError,
    SearchUnavailable,
    StableIdConflict,
)
from eeveetuber.storage.ids import new_decision_id, new_memory_id, new_revision_id
from eeveetuber.storage.models import (
    MemoryCandidateRow,
    MemoryDecisionRow,
    MemoryGenerationRow,
    MemoryRecordRow,
    MemoryRevisionRow,
)
from eeveetuber.storage.search import safe_fts_query


class SqliteMemoryRepository:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        fts_available: Callable[[], bool],
    ) -> None:
        self._session_factory = session_factory
        self._fts_available = fts_available

    def current_generation(self, namespace: str) -> int:
        with self._session_factory() as session:
            row = session.get(MemoryGenerationRow, namespace)
            return row.generation if row is not None else 0

    def add_candidate(self, candidate: MemoryCandidate) -> MemoryCandidate:
        with self._session_factory.begin() as session:
            existing = session.get(MemoryCandidateRow, candidate.candidate_id)
            if existing is not None:
                stored = _candidate(existing)
                if stored != candidate:
                    raise StableIdConflict(
                        f"candidate ID {candidate.candidate_id!r} has different data"
                    )
                return stored
            session.add(_candidate_row(candidate))
        return candidate

    def get_candidate(self, candidate_id: str) -> MemoryCandidate | None:
        with self._session_factory() as session:
            row = session.get(MemoryCandidateRow, candidate_id)
            return _candidate(row) if row is not None else None

    def list_candidates(
        self,
        namespace: str,
        *,
        status: CandidateStatus = CandidateStatus.PENDING,
        limit: int = 100,
    ) -> Sequence[MemoryCandidate]:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        with self._session_factory() as session:
            rows = session.scalars(
                select(MemoryCandidateRow)
                .where(
                    MemoryCandidateRow.namespace == namespace,
                    MemoryCandidateRow.status == status.value,
                )
                .order_by(MemoryCandidateRow.created_at, MemoryCandidateRow.candidate_id)
                .limit(limit)
            ).all()
            return tuple(_candidate(row) for row in rows)

    def get_record(self, memory_id: str) -> MemoryRecord | None:
        with self._session_factory() as session:
            record = session.get(MemoryRecordRow, memory_id)
            if record is None:
                return None
            revision = session.get(MemoryRevisionRow, record.current_revision_id)
            if revision is None:
                raise RuntimeError(f"memory {memory_id!r} points to a missing revision")
            return _memory_record(record, revision)

    def apply_decision(
        self,
        decision: PromotionDecision,
        *,
        approved_by: str | None = None,
    ) -> PromotionResult:
        with self._session_factory.begin() as session:
            candidate_row = session.get(MemoryCandidateRow, decision.candidate_id)
            if candidate_row is None:
                raise InvalidPromotion(f"candidate {decision.candidate_id!r} does not exist")
            candidate = _candidate(candidate_row)
            if candidate.status is CandidateStatus.COMMITTED:
                revision_row = session.scalar(
                    select(MemoryRevisionRow).where(
                        MemoryRevisionRow.candidate_id == candidate.candidate_id
                    )
                )
                if revision_row is None:
                    raise RuntimeError("committed candidate has no revision")
                return PromotionResult(
                    decision=decision,
                    candidate_status=CandidateStatus.COMMITTED,
                    generation=revision_row.generation,
                    revision=_revision(revision_row),
                )
            if candidate.status in {CandidateStatus.REJECTED, CandidateStatus.QUARANTINED}:
                raise InvalidPromotion(f"candidate is terminal: {candidate.status.value}")

            _validate_decision(candidate, decision, approved_by=approved_by)
            session.add(
                MemoryDecisionRow(
                    decision_id=new_decision_id(),
                    candidate_id=candidate.candidate_id,
                    action=decision.action.value,
                    reason_codes_json=list(decision.reason_codes),
                    policy_version=decision.policy_version,
                    decided_at=encode_datetime(decision.decided_at),
                    decided_by=decision.decided_by,
                    approved_by=approved_by,
                )
            )

            terminal_status = _non_commit_status(decision, approved_by=approved_by)
            if terminal_status is not None:
                candidate_row.status = terminal_status.value
                return PromotionResult(
                    decision=decision,
                    candidate_status=terminal_status,
                    generation=self._generation_in_session(session, candidate.namespace),
                )

            revision, generation = self._commit_candidate(session, candidate, candidate_row)
            return PromotionResult(
                decision=decision,
                candidate_status=CandidateStatus.COMMITTED,
                generation=generation,
                revision=revision,
            )

    def search(
        self,
        namespace: str,
        query: str,
        *,
        limit: int = 5,
        allowed_visibilities: frozenset[Visibility] = frozenset({Visibility.STREAM_SAFE}),
    ) -> Sequence[MemorySearchHit]:
        if not self._fts_available():
            raise SearchUnavailable("this SQLite build does not provide FTS5")
        if not 1 <= limit <= 50:
            raise ValueError("limit must be between 1 and 50")
        if not allowed_visibilities:
            return ()
        match_query = safe_fts_query(query)
        if not match_query:
            return ()
        visibility_parameters = {
            f"visibility_{index}": visibility.value
            for index, visibility in enumerate(sorted(allowed_visibilities, key=str))
        }
        visibility_clause = ", ".join(f":{name}" for name in visibility_parameters)
        statement = text(
            "SELECT f.memory_id, f.revision_id, f.namespace, f.subject, f.content, "
            "bm25(memory_fts) AS rank, r.visibility, r.sensitivity "
            "FROM memory_fts AS f "
            "JOIN memory_records AS m ON m.current_revision_id = f.revision_id "
            "JOIN memory_revisions AS r ON r.revision_id = f.revision_id "
            "WHERE memory_fts MATCH :query AND f.namespace = :namespace "
            "AND m.deleted_at IS NULL "
            f"AND r.visibility IN ({visibility_clause}) "
            "AND (r.valid_until IS NULL OR r.valid_until > :now) "
            "AND (r.ttl_seconds IS NULL OR "
            "datetime(r.created_at, '+' || r.ttl_seconds || ' seconds') > datetime(:now)) "
            "ORDER BY rank LIMIT :limit"
        )
        from eeveetuber.memory.models import utc_now

        parameters: dict[str, object] = {
            "query": match_query,
            "namespace": namespace,
            "now": encode_datetime(utc_now()),
            "limit": limit,
            **visibility_parameters,
        }
        with self._session_factory() as session:
            rows = session.execute(statement, parameters).mappings().all()
            return tuple(
                MemorySearchHit(
                    memory_id=str(row["memory_id"]),
                    revision_id=str(row["revision_id"]),
                    namespace=str(row["namespace"]),
                    subject=str(row["subject"]),
                    content=str(row["content"]),
                    score=-float(row["rank"]),
                    visibility=Visibility(str(row["visibility"])),
                    sensitivity=Sensitivity(str(row["sensitivity"])),
                )
                for row in rows
            )

    def _commit_candidate(
        self,
        session: Session,
        candidate: MemoryCandidate,
        candidate_row: MemoryCandidateRow,
    ) -> tuple[MemoryRevision, int]:
        if candidate.kind in {
            MemoryKind.PROCEDURAL_SKILL,
            MemoryKind.POLICY,
            MemoryKind.SECURITY,
        }:
            raise InvalidPromotion(f"{candidate.kind.value} cannot be committed as personal memory")

        memory_id = candidate.base_memory_id or new_memory_id()
        record_row = session.get(MemoryRecordRow, memory_id)
        if candidate.base_memory_id:
            if record_row is None:
                raise OptimisticConcurrencyError("base memory no longer exists")
            if record_row.current_revision_id != candidate.base_revision_id:
                raise OptimisticConcurrencyError(
                    "base revision is stale; review against the current memory revision"
                )
            if (
                record_row.kind != candidate.kind.value
                or record_row.scope_kind != candidate.scope.kind.value
                or record_row.scope_subject_id != candidate.scope.subject_id
            ):
                raise InvalidPromotion("a revision cannot change memory kind or scope")
        elif record_row is not None:
            raise StableIdConflict(f"generated memory ID {memory_id!r} already exists")

        generation = int(
            session.execute(
                text(
                    "INSERT INTO memory_generations(namespace, generation) VALUES (:namespace, 1) "
                    "ON CONFLICT(namespace) DO UPDATE SET generation = generation + 1 "
                    "RETURNING generation"
                ),
                {"namespace": candidate.namespace},
            ).scalar_one()
        )
        revision_id = new_revision_id()
        if record_row is None:
            record_row = MemoryRecordRow(
                memory_id=memory_id,
                namespace=candidate.namespace,
                kind=candidate.kind.value,
                scope_kind=candidate.scope.kind.value,
                scope_subject_id=candidate.scope.subject_id,
                subject=candidate.subject,
                current_revision_id=revision_id,
                created_at=encode_datetime(candidate.created_at),
            )
            session.add(record_row)
            session.flush()
        else:
            record_row.subject = candidate.subject
            record_row.current_revision_id = revision_id

        revision = MemoryRevision(
            revision_id=revision_id,
            memory_id=memory_id,
            namespace=candidate.namespace,
            generation=generation,
            parent_revision_id=candidate.base_revision_id,
            promoted_from_candidate_id=candidate.candidate_id,
            kind=candidate.kind,
            scope=candidate.scope,
            subject=candidate.subject,
            content=candidate.content,
            provenance=candidate.provenance,
            confidence=candidate.confidence,
            sensitivity=candidate.sensitivity,
            visibility=candidate.visibility,
            created_at=candidate.created_at,
            valid_from=candidate.valid_from,
            valid_until=candidate.valid_until,
            ttl_seconds=candidate.ttl_seconds,
            status=RevisionStatus.COMMITTED,
        )
        session.add(_revision_row(revision))
        candidate_row.status = CandidateStatus.COMMITTED.value
        session.flush()
        if self._fts_available():
            session.execute(
                text(
                    "INSERT INTO memory_fts(revision_id, memory_id, namespace, subject, content) "
                    "VALUES (:revision_id, :memory_id, :namespace, :subject, :content)"
                ),
                {
                    "revision_id": revision.revision_id,
                    "memory_id": revision.memory_id,
                    "namespace": revision.namespace,
                    "subject": revision.subject,
                    "content": revision.content,
                },
            )
        return revision, generation

    @staticmethod
    def _generation_in_session(session: Session, namespace: str) -> int:
        row = session.get(MemoryGenerationRow, namespace)
        return row.generation if row is not None else 0


def _validate_decision(
    candidate: MemoryCandidate,
    decision: PromotionDecision,
    *,
    approved_by: str | None,
) -> None:
    if decision.candidate_id != candidate.candidate_id:
        raise InvalidPromotion("decision candidate ID does not match")
    baseline = PromotionPolicy(version=decision.policy_version).decide(candidate)
    if baseline.action in {PromotionAction.REJECT, PromotionAction.QUARANTINE}:
        if decision.action is not baseline.action:
            raise InvalidPromotion(f"candidate must remain {baseline.action.value}")
    elif baseline.action is PromotionAction.REQUIRE_REVIEW:
        if decision.action is PromotionAction.AUTO_COMMIT:
            raise InvalidPromotion("review-required candidate cannot auto-commit")
    if decision.action is PromotionAction.REQUIRE_REVIEW and approved_by is not None:
        if not approved_by.strip():
            raise InvalidPromotion("approved_by cannot be blank")


def _non_commit_status(
    decision: PromotionDecision,
    *,
    approved_by: str | None,
) -> CandidateStatus | None:
    if decision.action is PromotionAction.REJECT:
        return CandidateStatus.REJECTED
    if decision.action is PromotionAction.QUARANTINE:
        return CandidateStatus.QUARANTINED
    if decision.action is PromotionAction.REQUIRE_REVIEW and approved_by is None:
        return CandidateStatus.REVIEW_REQUIRED
    return None


def _candidate_row(value: MemoryCandidate) -> MemoryCandidateRow:
    provenance = value.provenance
    return MemoryCandidateRow(
        candidate_id=value.candidate_id,
        namespace=value.namespace,
        kind=value.kind.value,
        scope_kind=value.scope.kind.value,
        scope_subject_id=value.scope.subject_id,
        subject=value.subject,
        content=value.content,
        source_kind=provenance.source_kind.value,
        source_id=provenance.source_id,
        source_trust=provenance.source_trust.value,
        source_actor_id=provenance.actor_id,
        source_observed_at=encode_datetime(provenance.observed_at),
        evidence_event_ids_json=list(provenance.evidence_event_ids),
        consented=int(provenance.consented),
        confidence=value.confidence,
        sensitivity=value.sensitivity.value,
        visibility=value.visibility.value,
        created_at=encode_datetime(value.created_at),
        valid_from=encode_optional_datetime(value.valid_from),
        valid_until=encode_optional_datetime(value.valid_until),
        ttl_seconds=value.ttl_seconds,
        base_memory_id=value.base_memory_id,
        base_revision_id=value.base_revision_id,
        contradiction_revision_ids_json=list(value.contradiction_revision_ids),
        status=value.status.value,
    )


def _candidate(row: MemoryCandidateRow) -> MemoryCandidate:
    return MemoryCandidate(
        candidate_id=row.candidate_id,
        namespace=row.namespace,
        kind=MemoryKind(row.kind),
        scope=MemoryScope(kind=ScopeKind(row.scope_kind), subject_id=row.scope_subject_id),
        subject=row.subject,
        content=row.content,
        provenance=_provenance(
            source_kind=row.source_kind,
            source_id=row.source_id,
            source_trust=row.source_trust,
            source_actor_id=row.source_actor_id,
            source_observed_at=row.source_observed_at,
            evidence_event_ids=row.evidence_event_ids_json,
            consented=row.consented,
        ),
        confidence=row.confidence,
        sensitivity=Sensitivity(row.sensitivity),
        visibility=Visibility(row.visibility),
        created_at=decode_datetime(row.created_at),
        valid_from=decode_optional_datetime(row.valid_from),
        valid_until=decode_optional_datetime(row.valid_until),
        ttl_seconds=row.ttl_seconds,
        base_memory_id=row.base_memory_id,
        base_revision_id=row.base_revision_id,
        contradiction_revision_ids=tuple(row.contradiction_revision_ids_json),
        status=CandidateStatus(row.status),
    )


def _revision_row(value: MemoryRevision) -> MemoryRevisionRow:
    provenance = value.provenance
    return MemoryRevisionRow(
        revision_id=value.revision_id,
        memory_id=value.memory_id,
        namespace=value.namespace,
        generation=value.generation,
        parent_revision_id=value.parent_revision_id,
        candidate_id=value.promoted_from_candidate_id,
        kind=value.kind.value,
        scope_kind=value.scope.kind.value,
        scope_subject_id=value.scope.subject_id,
        subject=value.subject,
        content=value.content,
        source_kind=provenance.source_kind.value,
        source_id=provenance.source_id,
        source_trust=provenance.source_trust.value,
        source_actor_id=provenance.actor_id,
        source_observed_at=encode_datetime(provenance.observed_at),
        evidence_event_ids_json=list(provenance.evidence_event_ids),
        consented=int(provenance.consented),
        confidence=value.confidence,
        sensitivity=value.sensitivity.value,
        visibility=value.visibility.value,
        valid_from=encode_optional_datetime(value.valid_from),
        valid_until=encode_optional_datetime(value.valid_until),
        ttl_seconds=value.ttl_seconds,
        status=value.status.value,
        created_at=encode_datetime(value.created_at),
    )


def _revision(row: MemoryRevisionRow) -> MemoryRevision:
    return MemoryRevision(
        revision_id=row.revision_id,
        memory_id=row.memory_id,
        namespace=row.namespace,
        generation=row.generation,
        parent_revision_id=row.parent_revision_id,
        promoted_from_candidate_id=row.candidate_id,
        kind=MemoryKind(row.kind),
        scope=MemoryScope(kind=ScopeKind(row.scope_kind), subject_id=row.scope_subject_id),
        subject=row.subject,
        content=row.content,
        provenance=_provenance(
            source_kind=row.source_kind,
            source_id=row.source_id,
            source_trust=row.source_trust,
            source_actor_id=row.source_actor_id,
            source_observed_at=row.source_observed_at,
            evidence_event_ids=row.evidence_event_ids_json,
            consented=row.consented,
        ),
        confidence=row.confidence,
        sensitivity=Sensitivity(row.sensitivity),
        visibility=Visibility(row.visibility),
        valid_from=decode_optional_datetime(row.valid_from),
        valid_until=decode_optional_datetime(row.valid_until),
        ttl_seconds=row.ttl_seconds,
        status=RevisionStatus(row.status),
        created_at=decode_datetime(row.created_at),
    )


def _memory_record(row: MemoryRecordRow, revision: MemoryRevisionRow) -> MemoryRecord:
    return MemoryRecord(
        memory_id=row.memory_id,
        namespace=row.namespace,
        kind=MemoryKind(row.kind),
        scope=MemoryScope(kind=ScopeKind(row.scope_kind), subject_id=row.scope_subject_id),
        subject=row.subject,
        created_at=decode_datetime(row.created_at),
        current_revision=_revision(revision),
        deleted_at=decode_optional_datetime(row.deleted_at),
    )


def _provenance(
    *,
    source_kind: str,
    source_id: str,
    source_trust: str,
    source_actor_id: str | None,
    source_observed_at: str,
    evidence_event_ids: list[str],
    consented: int,
) -> MemoryProvenance:
    return MemoryProvenance(
        source_kind=SourceKind(source_kind),
        source_id=source_id,
        source_trust=SourceTrust(source_trust),
        observed_at=decode_datetime(source_observed_at),
        actor_id=source_actor_id,
        evidence_event_ids=tuple(evidence_event_ids),
        consented=bool(consented),
    )

