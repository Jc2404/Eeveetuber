"""Deterministic presentation arbitration with leases and cancellation.

The scheduler is intentionally synchronous and single-owner.  A session actor
may call it from async code, but no internal task or wall-clock timer is created;
the owner calls :meth:`tick` and dispatches the returned events.  This makes cue
ordering replayable and unit tests independent of real time.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict, defaultdict, deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .intents import PresentationLayer
from .presentation import (
    ActiveLease,
    AudioTimelineMarker,
    PresentationCue,
    PresentationEvent,
    PresentationEventKind,
    ScheduleResult,
    ScheduleStatus,
    StopReason,
)

Clock = Callable[[], float]
NeutralFactory = Callable[[PresentationLayer, int, float], PresentationCue]


@dataclass(frozen=True, slots=True)
class RateLimitPolicy:
    max_starts: int
    window_s: float

    def __post_init__(self) -> None:
        if self.max_starts < 1:
            raise ValueError("max_starts must be positive")
        if not math.isfinite(self.window_s) or self.window_s <= 0.0:
            raise ValueError("window_s must be finite and greater than zero")


@dataclass(frozen=True, slots=True)
class SchedulerPolicy:
    global_rate_limit: RateLimitPolicy | None = None
    layer_rate_limits: Mapping[PresentationLayer, RateLimitPolicy] = field(default_factory=dict)
    max_pending: int = 256
    max_audio_markers: int = 1024

    def __post_init__(self) -> None:
        if self.max_pending < 1:
            raise ValueError("max_pending must be positive")
        if self.max_audio_markers < 1:
            raise ValueError("max_audio_markers must be positive")
        limits = dict(sorted(self.layer_rate_limits.items(), key=lambda item: item[0].value))
        object.__setattr__(self, "layer_rate_limits", MappingProxyType(limits))


@dataclass(frozen=True, slots=True)
class SchedulerBatch:
    """Outcomes produced together by a timeline marker or scheduler tick."""

    results: tuple[ScheduleResult, ...] = ()
    events: tuple[PresentationEvent, ...] = ()


@dataclass(slots=True)
class _PendingCue:
    cue: PresentationCue
    sequence: int
    due_at: float | None = None


class PresentationScheduler:
    """Own active layers, resource leases, timing bindings, and safe fallback."""

    def __init__(
        self,
        *,
        generation: int = 0,
        clock: Clock = time.monotonic,
        policy: SchedulerPolicy | None = None,
        neutral_factory: NeutralFactory | None = None,
    ) -> None:
        if generation < 0:
            raise ValueError("generation must be non-negative")
        self._generation = generation
        self._clock = clock
        self._policy = policy or SchedulerPolicy()
        self._neutral_factory = neutral_factory
        self._connected = True
        self._active: dict[str, ActiveLease] = {}
        self._active_by_layer: dict[PresentationLayer, str] = {}
        self._pending: dict[str, _PendingCue] = {}
        self._audio_markers: OrderedDict[
            tuple[str, object, str | None], AudioTimelineMarker
        ] = OrderedDict()
        self._cooldown_until: dict[str, float] = {}
        self._global_starts: deque[float] = deque()
        self._layer_starts: dict[PresentationLayer, deque[float]] = defaultdict(deque)
        self._needs_neutral: set[PresentationLayer] = set()
        self._sequence = 0

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def active_leases(self) -> tuple[ActiveLease, ...]:
        return tuple(
            sorted(
                self._active.values(),
                key=lambda lease: (lease.cue.layer.value, lease.cue.cue_id),
            )
        )

    @property
    def pending_cue_ids(self) -> tuple[str, ...]:
        return tuple(
            pending.cue.cue_id
            for pending in sorted(self._pending.values(), key=lambda item: item.sequence)
        )

    def current_cue(self, layer: PresentationLayer) -> PresentationCue | None:
        cue_id = self._active_by_layer.get(layer)
        return self._active[cue_id].cue if cue_id is not None else None

    def accepts_adapter_result(self, cue_id: str, generation: int) -> bool:
        """Gate late adapter acknowledgements with the session generation."""

        lease = self._active.get(cue_id)
        return (
            self._connected
            and generation == self._generation
            and lease is not None
            and lease.cue.generation == generation
        )

    def submit(self, cue: PresentationCue) -> ScheduleResult:
        now = self._now()
        events = list(self._expire(now))

        rejection = self._validate_submission(cue, now)
        if rejection is not None:
            events.extend(self._restore_neutrals(now))
            return ScheduleResult(
                status=rejection,
                cue=cue,
                events=tuple(events),
                detail=self._status_detail(rejection),
            )

        if cue.audio_binding is not None:
            marker = self._audio_markers.get(self._audio_key(cue))
            due_at = None if marker is None else marker.occurred_at + cue.audio_binding.offset_s
            if len(self._pending) >= self._policy.max_pending:
                events.extend(self._restore_neutrals(now))
                return ScheduleResult(
                    status=ScheduleStatus.REJECTED_QUEUE_FULL,
                    cue=cue,
                    events=tuple(events),
                    detail="audio-bound cue queue is full",
                )
            pending = _PendingCue(cue=cue, sequence=self._next_sequence(), due_at=due_at)
            self._pending[cue.cue_id] = pending
            if due_at is None or due_at > now:
                events.extend(self._restore_neutrals(now))
                return ScheduleResult(
                    status=ScheduleStatus.PENDING_AUDIO,
                    cue=cue,
                    events=tuple(events),
                    detail=("waiting for audio marker" if due_at is None else f"bound for {due_at}"),
                )
            del self._pending[cue.cue_id]

        result = self._attempt_start(cue, now)
        events.extend(result.events)
        events.extend(self._restore_neutrals(now))
        return ScheduleResult(result.status, cue, tuple(events), result.detail)

    def notify_audio(self, marker: AudioTimelineMarker) -> SchedulerBatch:
        """Bind matching pending cues to one audio/playback timeline marker."""

        if not self._connected or marker.generation != self._generation:
            return SchedulerBatch()
        now = self._now()
        events = list(self._expire(now))
        self._audio_markers[marker.key] = marker
        self._audio_markers.move_to_end(marker.key)
        while len(self._audio_markers) > self._policy.max_audio_markers:
            self._audio_markers.popitem(last=False)

        for pending in self._pending.values():
            binding = pending.cue.audio_binding
            if binding is not None and self._audio_key(pending.cue) == marker.key:
                pending.due_at = marker.occurred_at + binding.offset_s

        results, start_events = self._start_due(now)
        events.extend(start_events)
        events.extend(self._restore_neutrals(now))
        return SchedulerBatch(tuple(results), tuple(events))

    def tick(self) -> SchedulerBatch:
        """Expire leases and start audio-bound cues whose due time has arrived."""

        now = self._now()
        events = list(self._expire(now))
        results, start_events = self._start_due(now)
        events.extend(start_events)
        events.extend(self._restore_neutrals(now))
        return SchedulerBatch(tuple(results), tuple(events))

    def advance_generation(self, generation: int) -> tuple[PresentationEvent, ...]:
        """Atomically invalidate all work from older foreground turns."""

        if generation < self._generation:
            raise ValueError("generation cannot move backwards")
        if generation == self._generation:
            return ()
        now = self._now()
        self._generation = generation
        self._pending.clear()
        self._audio_markers.clear()

        events: list[PresentationEvent] = []
        for lease in self._sorted_active():
            self._remove_active(lease.cue.cue_id)
            self._needs_neutral.add(lease.cue.layer)
            events.append(
                self._stop_event(lease.cue, now, StopReason.GENERATION_ADVANCED)
            )
        events.extend(self._restore_neutrals(now))
        return tuple(events)

    def cancel(
        self,
        cue_id: str,
        *,
        reason: StopReason = StopReason.CANCELLED,
    ) -> tuple[PresentationEvent, ...]:
        """Cancel one pending or active cue; unknown IDs are an idempotent no-op."""

        now = self._now()
        events = list(self._expire(now))
        if self._pending.pop(cue_id, None) is not None:
            events.extend(self._restore_neutrals(now))
            return tuple(events)
        lease = self._remove_active(cue_id)
        if lease is not None:
            self._needs_neutral.add(lease.cue.layer)
            events.append(self._stop_event(lease.cue, now, reason))
        events.extend(self._restore_neutrals(now))
        return tuple(events)

    def reset_to_neutral(self) -> tuple[PresentationEvent, ...]:
        """Operator-safe reset: clear all leases/pending work and restore baselines."""

        now = self._now()
        self._pending.clear()
        events: list[PresentationEvent] = []
        for lease in self._sorted_active():
            self._remove_active(lease.cue.cue_id)
            events.append(self._stop_event(lease.cue, now, StopReason.CANCELLED))
        self._needs_neutral.update(PresentationLayer)
        events.extend(self._restore_neutrals(now))
        return tuple(events)

    def disconnect(
        self,
        *,
        reason: StopReason = StopReason.DISCONNECTED,
    ) -> tuple[PresentationEvent, ...]:
        """Drop volatile cues without pretending neutral was rendered offline."""

        if reason not in {StopReason.DISCONNECTED, StopReason.ADAPTER_FAILURE}:
            raise ValueError("disconnect reason must describe adapter availability")
        if not self._connected:
            return ()
        now = self._now()
        self._connected = False
        self._pending.clear()
        self._audio_markers.clear()
        self._needs_neutral.clear()
        events: list[PresentationEvent] = []
        for lease in self._sorted_active():
            self._remove_active(lease.cue.cue_id)
            events.append(self._stop_event(lease.cue, now, reason))
        return tuple(events)

    def reconnect(self) -> tuple[PresentationEvent, ...]:
        """Restore all neutral layers after a renderer reconnects."""

        if self._connected:
            return ()
        now = self._now()
        self._connected = True
        self._needs_neutral.update(PresentationLayer)
        return tuple(self._restore_neutrals(now))

    def adapter_failed(self) -> tuple[PresentationEvent, ...]:
        return self.disconnect(reason=StopReason.ADAPTER_FAILURE)

    def _now(self) -> float:
        now = self._clock()
        if not math.isfinite(now):
            raise ValueError("scheduler clock must return a finite value")
        return now

    def _validate_submission(
        self,
        cue: PresentationCue,
        now: float,
    ) -> ScheduleStatus | None:
        if not self._connected:
            return ScheduleStatus.REJECTED_DISCONNECTED
        if cue.generation < self._generation:
            return ScheduleStatus.REJECTED_STALE_GENERATION
        if cue.generation > self._generation:
            return ScheduleStatus.REJECTED_FUTURE_GENERATION
        if cue.not_after is not None and now > cue.not_after:
            return ScheduleStatus.REJECTED_EXPIRED
        if cue.cue_id in self._active or cue.cue_id in self._pending:
            return ScheduleStatus.REJECTED_DUPLICATE
        return None

    @staticmethod
    def _status_detail(status: ScheduleStatus) -> str:
        return {
            ScheduleStatus.REJECTED_DISCONNECTED: "avatar adapter is disconnected",
            ScheduleStatus.REJECTED_STALE_GENERATION: "cue belongs to an older generation",
            ScheduleStatus.REJECTED_FUTURE_GENERATION: "advance scheduler generation first",
            ScheduleStatus.REJECTED_EXPIRED: "cue TTL elapsed before admission",
            ScheduleStatus.REJECTED_DUPLICATE: "cue_id is already active or pending",
        }.get(status, status.value)

    def _attempt_start(self, cue: PresentationCue, now: float) -> ScheduleResult:
        if cue.not_after is not None and now > cue.not_after:
            return ScheduleResult(
                ScheduleStatus.REJECTED_EXPIRED,
                cue,
                detail="cue TTL elapsed before its audio-bound start",
            )
        if cue.generation != self._generation:
            status = (
                ScheduleStatus.REJECTED_STALE_GENERATION
                if cue.generation < self._generation
                else ScheduleStatus.REJECTED_FUTURE_GENERATION
            )
            return ScheduleResult(status, cue, detail=self._status_detail(status))
        if not self._connected:
            return ScheduleResult(
                ScheduleStatus.REJECTED_DISCONNECTED,
                cue,
                detail="avatar adapter is disconnected",
            )

        conflicts = self._conflicting_leases(cue)
        blockers = [lease for lease in conflicts if lease.cue.priority > cue.priority]
        if blockers:
            blocker = max(blockers, key=lambda lease: (lease.cue.priority, lease.cue.cue_id))
            return ScheduleResult(
                ScheduleStatus.REJECTED_PRIORITY,
                cue,
                detail=f"blocked by {blocker.cue.cue_id}",
            )

        if not cue.limits_exempt and not cue.neutral:
            if cue.cooldown_key is not None and now < self._cooldown_until.get(
                cue.cooldown_key, float("-inf")
            ):
                return ScheduleResult(
                    ScheduleStatus.REJECTED_COOLDOWN,
                    cue,
                    detail=f"cooldown active for {cue.cooldown_key}",
                )
            if not self._within_rate_limits(cue.layer, now):
                return ScheduleResult(
                    ScheduleStatus.REJECTED_RATE_LIMIT,
                    cue,
                    detail="presentation start rate exceeded",
                )

        events: list[PresentationEvent] = []
        for lease in conflicts:
            self._remove_active(lease.cue.cue_id)
            if lease.cue.layer is not cue.layer:
                self._needs_neutral.add(lease.cue.layer)
            events.append(
                self._stop_event(
                    lease.cue,
                    now,
                    StopReason.PREEMPTED,
                    replaced_by=cue.cue_id,
                )
            )

        expires_at = None if cue.lease_s is None else now + cue.lease_s
        lease = ActiveLease(cue=cue, started_at=now, expires_at=expires_at)
        self._active[cue.cue_id] = lease
        self._active_by_layer[cue.layer] = cue.cue_id
        self._needs_neutral.discard(cue.layer)
        events.append(self._start_event(cue, now))

        if not cue.limits_exempt and not cue.neutral:
            if cue.cooldown_key is not None and cue.cooldown_s > 0.0:
                self._cooldown_until[cue.cooldown_key] = now + cue.cooldown_s
            self._record_start(cue.layer, now)
        return ScheduleResult(ScheduleStatus.STARTED, cue, tuple(events))

    def _start_due(
        self,
        now: float,
    ) -> tuple[list[ScheduleResult], list[PresentationEvent]]:
        due = sorted(
            (
                pending
                for pending in self._pending.values()
                if pending.due_at is not None and pending.due_at <= now
            ),
            key=lambda pending: (
                pending.due_at if pending.due_at is not None else float("inf"),
                -int(pending.cue.priority),
                pending.sequence,
            ),
        )
        results: list[ScheduleResult] = []
        events: list[PresentationEvent] = []
        for pending in due:
            if self._pending.pop(pending.cue.cue_id, None) is None:
                continue
            result = self._attempt_start(pending.cue, now)
            results.append(result)
            events.extend(result.events)
        return results, events

    def _expire(self, now: float) -> tuple[PresentationEvent, ...]:
        events: list[PresentationEvent] = []
        for lease in self._sorted_active():
            if lease.expires_at is not None and lease.expires_at <= now:
                self._remove_active(lease.cue.cue_id)
                self._needs_neutral.add(lease.cue.layer)
                events.append(self._stop_event(lease.cue, now, StopReason.LEASE_EXPIRED))

        expired_pending = [
            cue_id
            for cue_id, pending in self._pending.items()
            if pending.cue.not_after is not None and now > pending.cue.not_after
        ]
        for cue_id in expired_pending:
            del self._pending[cue_id]

        expired_cooldowns = [
            key for key, until in self._cooldown_until.items() if until <= now
        ]
        for key in expired_cooldowns:
            del self._cooldown_until[key]
        return tuple(events)

    def _restore_neutrals(self, now: float) -> list[PresentationEvent]:
        if not self._connected or self._neutral_factory is None:
            return []
        events: list[PresentationEvent] = []
        for layer in sorted(tuple(self._needs_neutral), key=lambda item: item.value):
            if layer in self._active_by_layer:
                self._needs_neutral.discard(layer)
                continue
            cue = self._neutral_factory(layer, self._generation, now)
            if cue.layer is not layer or cue.generation != self._generation or not cue.neutral:
                raise ValueError("neutral_factory returned an invalid neutral cue")
            if self._conflicting_leases(cue):
                continue
            lease = ActiveLease(cue=cue, started_at=now, expires_at=None)
            self._active[cue.cue_id] = lease
            self._active_by_layer[layer] = cue.cue_id
            self._needs_neutral.discard(layer)
            events.append(self._start_event(cue, now))
        return events

    def _conflicting_leases(self, cue: PresentationCue) -> list[ActiveLease]:
        conflicts = [
            lease
            for lease in self._active.values()
            if lease.cue.layer is cue.layer
            or bool(lease.cue.resources.intersection(cue.resources))
        ]
        return sorted(
            conflicts,
            key=lambda lease: (
                -int(lease.cue.priority),
                lease.started_at,
                lease.cue.cue_id,
            ),
        )

    def _within_rate_limits(self, layer: PresentationLayer, now: float) -> bool:
        global_limit = self._policy.global_rate_limit
        if global_limit is not None:
            self._prune_window(self._global_starts, now, global_limit.window_s)
            if len(self._global_starts) >= global_limit.max_starts:
                return False
        layer_limit = self._policy.layer_rate_limits.get(layer)
        if layer_limit is not None:
            starts = self._layer_starts[layer]
            self._prune_window(starts, now, layer_limit.window_s)
            if len(starts) >= layer_limit.max_starts:
                return False
        return True

    def _record_start(self, layer: PresentationLayer, now: float) -> None:
        if self._policy.global_rate_limit is not None:
            self._global_starts.append(now)
        if layer in self._policy.layer_rate_limits:
            self._layer_starts[layer].append(now)

    @staticmethod
    def _prune_window(starts: deque[float], now: float, window_s: float) -> None:
        cutoff = now - window_s
        while starts and starts[0] <= cutoff:
            starts.popleft()

    def _remove_active(self, cue_id: str) -> ActiveLease | None:
        lease = self._active.pop(cue_id, None)
        if lease is not None and self._active_by_layer.get(lease.cue.layer) == cue_id:
            del self._active_by_layer[lease.cue.layer]
        return lease

    def _sorted_active(self) -> tuple[ActiveLease, ...]:
        return tuple(
            sorted(
                self._active.values(),
                key=lambda lease: (lease.cue.layer.value, lease.cue.cue_id),
            )
        )

    def _start_event(self, cue: PresentationCue, now: float) -> PresentationEvent:
        return PresentationEvent(
            sequence=self._next_sequence(),
            kind=PresentationEventKind.STARTED,
            cue=cue,
            occurred_at=now,
        )

    def _stop_event(
        self,
        cue: PresentationCue,
        now: float,
        reason: StopReason,
        *,
        replaced_by: str | None = None,
    ) -> PresentationEvent:
        return PresentationEvent(
            sequence=self._next_sequence(),
            kind=PresentationEventKind.STOPPED,
            cue=cue,
            occurred_at=now,
            reason=reason,
            replaced_by=replaced_by,
        )

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    @staticmethod
    def _audio_key(cue: PresentationCue) -> tuple[str, object, str | None]:
        binding = cue.audio_binding
        if binding is None:
            raise ValueError("cue has no audio binding")
        return (binding.utterance_id, binding.anchor, binding.marker_id)


__all__ = [
    "Clock",
    "NeutralFactory",
    "PresentationScheduler",
    "RateLimitPolicy",
    "SchedulerBatch",
    "SchedulerPolicy",
]
