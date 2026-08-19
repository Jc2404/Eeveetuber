from dataclasses import replace

import pytest

from eeveetuber.avatar import (
    AudioAnchor,
    AudioTimelineBinding,
    AudioTimelineMarker,
    CuePriority,
    CueSource,
    PresentationCue,
    PresentationEventKind,
    PresentationLayer,
    PresentationScheduler,
    RateLimitPolicy,
    SchedulerPolicy,
    ScheduleStatus,
    StopReason,
)


class FakeClock:
    def __init__(self, initial: float = 0.0) -> None:
        self.value = initial

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _cue(
    cue_id: str,
    *,
    layer: PresentationLayer = PresentationLayer.GESTURE,
    generation: int = 0,
    priority: CuePriority = CuePriority.MODEL,
    requested_at: float = 0.0,
    not_after: float | None = 100.0,
    lease_s: float | None = 10.0,
    resources: frozenset[str] = frozenset(),
    cooldown_key: str | None = None,
    cooldown_s: float = 0.0,
    audio_binding: AudioTimelineBinding | None = None,
    limits_exempt: bool = False,
) -> PresentationCue:
    return PresentationCue(
        cue_id=cue_id,
        generation=generation,
        layer=layer,
        semantic_key=f"{layer.value}.test",
        adapter_action=f"action:{cue_id}",
        intensity=1.0,
        priority=priority,
        requested_at=requested_at,
        not_after=not_after,
        lease_s=lease_s,
        resources=resources,
        cooldown_key=cooldown_key,
        cooldown_s=cooldown_s,
        audio_binding=audio_binding,
        source=CueSource.MODEL,
        limits_exempt=limits_exempt,
    )


def _neutral(
    layer: PresentationLayer,
    generation: int,
    requested_at: float,
) -> PresentationCue:
    return PresentationCue(
        cue_id=f"neutral:{layer.value}:{generation}",
        generation=generation,
        layer=layer,
        semantic_key=f"{layer.value}.neutral",
        adapter_action=f"neutral:{layer.value}",
        intensity=1.0,
        priority=CuePriority.REACTIVE_IDLE,
        requested_at=requested_at,
        not_after=None,
        lease_s=None,
        source=CueSource.REACTIVE_IDLE,
        neutral=True,
        limits_exempt=True,
    )


def test_priority_arbitration_rejects_lower_and_new_equal_priority_wins() -> None:
    clock = FakeClock()
    scheduler = PresentationScheduler(clock=clock)
    incumbent = _cue("incumbent", priority=CuePriority.MODEL)

    assert scheduler.submit(incumbent).status is ScheduleStatus.STARTED
    blocked = scheduler.submit(_cue("blocked", priority=CuePriority.GAME_EVENT))
    replacement = scheduler.submit(_cue("replacement", priority=CuePriority.MODEL))

    assert blocked.status is ScheduleStatus.REJECTED_PRIORITY
    assert replacement.status is ScheduleStatus.STARTED
    assert [event.kind for event in replacement.events] == [
        PresentationEventKind.STOPPED,
        PresentationEventKind.STARTED,
    ]
    assert replacement.events[0].reason is StopReason.PREEMPTED
    assert replacement.events[0].replaced_by == "replacement"
    assert scheduler.current_cue(PresentationLayer.GESTURE) == replacement.cue


def test_shared_resource_arbitration_works_across_layers() -> None:
    clock = FakeClock()
    scheduler = PresentationScheduler(clock=clock, neutral_factory=_neutral)
    affect = _cue(
        "affect",
        layer=PresentationLayer.AFFECT,
        priority=CuePriority.SCRIPTED_SHOW,
        resources=frozenset({"head"}),
    )
    assert scheduler.submit(affect).status is ScheduleStatus.STARTED

    blocked = scheduler.submit(
        _cue(
            "gaze-low",
            layer=PresentationLayer.GAZE,
            priority=CuePriority.MODEL,
            resources=frozenset({"head"}),
        )
    )
    override = scheduler.submit(
        _cue(
            "gaze-high",
            layer=PresentationLayer.GAZE,
            priority=CuePriority.OPERATOR_OVERRIDE,
            resources=frozenset({"head"}),
            limits_exempt=True,
        )
    )

    assert blocked.status is ScheduleStatus.REJECTED_PRIORITY
    assert override.status is ScheduleStatus.STARTED
    assert override.events[0].cue.cue_id == "affect"
    assert override.events[0].reason is StopReason.PREEMPTED
    assert scheduler.current_cue(PresentationLayer.GAZE).cue_id == "gaze-high"
    assert scheduler.current_cue(PresentationLayer.AFFECT).neutral


def test_lease_expiry_stops_cue_and_restores_neutral() -> None:
    clock = FakeClock()
    scheduler = PresentationScheduler(clock=clock, neutral_factory=_neutral)
    scheduler.submit(_cue("short", lease_s=1.0))

    clock.advance(1.0)
    batch = scheduler.tick()

    assert [(event.kind, event.reason) for event in batch.events] == [
        (PresentationEventKind.STOPPED, StopReason.LEASE_EXPIRED),
        (PresentationEventKind.STARTED, None),
    ]
    assert scheduler.current_cue(PresentationLayer.GESTURE).neutral


def test_generation_advance_cancels_active_and_audio_pending_atomically() -> None:
    clock = FakeClock(5.0)
    scheduler = PresentationScheduler(generation=2, clock=clock, neutral_factory=_neutral)
    active = _cue("active", generation=2, requested_at=5.0, not_after=10.0)
    pending = _cue(
        "pending",
        generation=2,
        requested_at=5.0,
        not_after=10.0,
        audio_binding=AudioTimelineBinding("utt-1"),
    )
    scheduler.submit(active)
    scheduler.submit(pending)

    events = scheduler.advance_generation(3)

    assert scheduler.generation == 3
    assert scheduler.pending_cue_ids == ()
    assert events[0].cue.cue_id == "active"
    assert events[0].reason is StopReason.GENERATION_ADVANCED
    assert scheduler.current_cue(PresentationLayer.GESTURE).generation == 3
    assert not scheduler.accepts_adapter_result("active", 2)


def test_stale_and_future_generations_are_rejected_until_owner_advances() -> None:
    scheduler = PresentationScheduler(generation=4, clock=FakeClock())

    stale = scheduler.submit(_cue("stale", generation=3))
    future = scheduler.submit(_cue("future", generation=5))

    assert stale.status is ScheduleStatus.REJECTED_STALE_GENERATION
    assert future.status is ScheduleStatus.REJECTED_FUTURE_GENERATION
    assert scheduler.active_leases == ()


def test_generation_cannot_move_backwards() -> None:
    scheduler = PresentationScheduler(generation=2, clock=FakeClock())
    with pytest.raises(ValueError, match="backwards"):
        scheduler.advance_generation(1)


def test_cooldown_is_deterministic_and_neutral_does_not_block_retry() -> None:
    clock = FakeClock()
    scheduler = PresentationScheduler(clock=clock, neutral_factory=_neutral)
    first = _cue(
        "wave-1",
        lease_s=0.1,
        cooldown_key="gesture.wave",
        cooldown_s=2.0,
    )
    scheduler.submit(first)
    clock.advance(0.1)
    scheduler.tick()

    retry = scheduler.submit(
        _cue("wave-2", cooldown_key="gesture.wave", cooldown_s=2.0)
    )
    assert retry.status is ScheduleStatus.REJECTED_COOLDOWN
    assert scheduler.current_cue(PresentationLayer.GESTURE).neutral

    clock.advance(1.9)
    accepted = scheduler.submit(
        _cue("wave-3", requested_at=2.0, cooldown_key="gesture.wave", cooldown_s=2.0)
    )
    assert accepted.status is ScheduleStatus.STARTED


def test_global_rate_limit_recovers_at_exact_window_boundary() -> None:
    clock = FakeClock()
    scheduler = PresentationScheduler(
        clock=clock,
        policy=SchedulerPolicy(global_rate_limit=RateLimitPolicy(2, 5.0)),
    )
    assert scheduler.submit(_cue("one", layer=PresentationLayer.AFFECT)).status is ScheduleStatus.STARTED
    assert scheduler.submit(_cue("two", layer=PresentationLayer.GESTURE)).status is ScheduleStatus.STARTED
    denied = scheduler.submit(_cue("three", layer=PresentationLayer.GAZE))
    assert denied.status is ScheduleStatus.REJECTED_RATE_LIMIT

    clock.advance(5.0)
    accepted = scheduler.submit(
        _cue("four", layer=PresentationLayer.POSTURE, requested_at=5.0)
    )
    assert accepted.status is ScheduleStatus.STARTED


def test_per_layer_rate_limit_and_exemption() -> None:
    clock = FakeClock()
    scheduler = PresentationScheduler(
        clock=clock,
        policy=SchedulerPolicy(
            layer_rate_limits={
                PresentationLayer.GESTURE: RateLimitPolicy(max_starts=1, window_s=10.0)
            }
        ),
    )
    scheduler.submit(_cue("one"))
    denied = scheduler.submit(_cue("two", priority=CuePriority.SCRIPTED_SHOW))
    exempt = scheduler.submit(
        _cue(
            "operator",
            priority=CuePriority.OPERATOR_OVERRIDE,
            limits_exempt=True,
        )
    )

    assert denied.status is ScheduleStatus.REJECTED_RATE_LIMIT
    assert exempt.status is ScheduleStatus.STARTED


def test_audio_binding_waits_for_marker_and_offset() -> None:
    clock = FakeClock(10.0)
    scheduler = PresentationScheduler(clock=clock)
    cue = _cue(
        "on-word",
        requested_at=10.0,
        not_after=20.0,
        audio_binding=AudioTimelineBinding(
            utterance_id="utt-1",
            anchor=AudioAnchor.WORD,
            marker_id="word-4",
            offset_s=0.5,
        ),
    )

    submitted = scheduler.submit(cue)
    marker_batch = scheduler.notify_audio(
        AudioTimelineMarker(
            utterance_id="utt-1",
            generation=0,
            anchor=AudioAnchor.WORD,
            marker_id="word-4",
            occurred_at=10.0,
        )
    )

    assert submitted.status is ScheduleStatus.PENDING_AUDIO
    assert marker_batch.results == ()
    assert scheduler.pending_cue_ids == ("on-word",)
    clock.advance(0.49)
    assert scheduler.tick().results == ()
    clock.advance(0.01)
    due = scheduler.tick()
    assert due.results[0].status is ScheduleStatus.STARTED
    assert due.events[-1].cue.cue_id == "on-word"


def test_marker_seen_before_submission_binds_immediately() -> None:
    clock = FakeClock(3.0)
    scheduler = PresentationScheduler(clock=clock)
    scheduler.notify_audio(
        AudioTimelineMarker("utt", 0, AudioAnchor.UTTERANCE_START, occurred_at=3.0)
    )

    result = scheduler.submit(
        _cue(
            "late-plan",
            requested_at=3.0,
            not_after=5.0,
            audio_binding=AudioTimelineBinding("utt"),
        )
    )

    assert result.status is ScheduleStatus.STARTED


def test_stale_audio_marker_does_not_bind_current_pending_cue() -> None:
    clock = FakeClock()
    scheduler = PresentationScheduler(generation=2, clock=clock)
    scheduler.submit(
        _cue(
            "current",
            generation=2,
            audio_binding=AudioTimelineBinding("utt"),
        )
    )

    batch = scheduler.notify_audio(
        AudioTimelineMarker("utt", 1, AudioAnchor.UTTERANCE_START, occurred_at=0.0)
    )

    assert batch == type(batch)()
    assert scheduler.pending_cue_ids == ("current",)


def test_audio_pending_cue_expires_by_ttl_and_never_starts() -> None:
    clock = FakeClock()
    scheduler = PresentationScheduler(clock=clock)
    scheduler.submit(
        _cue(
            "expires",
            not_after=0.5,
            audio_binding=AudioTimelineBinding("utt"),
        )
    )
    clock.advance(0.51)

    scheduler.tick()
    scheduler.notify_audio(
        AudioTimelineMarker("utt", 0, AudioAnchor.UTTERANCE_START, occurred_at=0.51)
    )

    assert scheduler.pending_cue_ids == ()
    assert scheduler.active_leases == ()


def test_audio_pending_queue_is_bounded() -> None:
    scheduler = PresentationScheduler(
        clock=FakeClock(),
        policy=SchedulerPolicy(max_pending=1),
    )
    first = scheduler.submit(
        _cue("first", audio_binding=AudioTimelineBinding("utt-1"))
    )
    second = scheduler.submit(
        _cue("second", audio_binding=AudioTimelineBinding("utt-2"))
    )

    assert first.status is ScheduleStatus.PENDING_AUDIO
    assert second.status is ScheduleStatus.REJECTED_QUEUE_FULL


def test_cancel_is_idempotent_and_restores_neutral() -> None:
    scheduler = PresentationScheduler(clock=FakeClock(), neutral_factory=_neutral)
    scheduler.submit(_cue("wave"))

    events = scheduler.cancel("wave")

    assert events[0].reason is StopReason.CANCELLED
    assert events[1].cue.neutral
    assert scheduler.cancel("does-not-exist") == ()


def test_reset_to_neutral_covers_every_layer() -> None:
    scheduler = PresentationScheduler(clock=FakeClock(), neutral_factory=_neutral)
    scheduler.submit(_cue("gesture"))

    scheduler.reset_to_neutral()

    assert {lease.cue.layer for lease in scheduler.active_leases} == set(PresentationLayer)
    assert all(lease.cue.neutral for lease in scheduler.active_leases)


def test_disconnect_drops_cues_and_reconnect_restores_neutral() -> None:
    scheduler = PresentationScheduler(clock=FakeClock(), neutral_factory=_neutral)
    scheduler.submit(_cue("active"))
    scheduler.submit(
        _cue("pending", audio_binding=AudioTimelineBinding("utt"))
    )

    stopped = scheduler.disconnect()
    rejected = scheduler.submit(_cue("offline"))
    restarted = scheduler.reconnect()

    assert stopped[0].reason is StopReason.DISCONNECTED
    assert scheduler.pending_cue_ids == ()
    assert rejected.status is ScheduleStatus.REJECTED_DISCONNECTED
    assert len(restarted) == len(PresentationLayer)
    assert all(event.cue.neutral for event in restarted)


def test_adapter_failure_uses_distinct_stop_reason() -> None:
    scheduler = PresentationScheduler(clock=FakeClock())
    scheduler.submit(_cue("active"))

    events = scheduler.adapter_failed()

    assert events[0].reason is StopReason.ADAPTER_FAILURE


def test_duplicate_and_elapsed_ttl_are_rejected() -> None:
    clock = FakeClock()
    scheduler = PresentationScheduler(clock=clock)
    cue = _cue("same")
    scheduler.submit(cue)

    assert scheduler.submit(cue).status is ScheduleStatus.REJECTED_DUPLICATE
    clock.advance(2.0)
    expired = replace(cue, cue_id="old", not_after=1.0)
    assert scheduler.submit(expired).status is ScheduleStatus.REJECTED_EXPIRED


def test_event_sequence_is_strictly_increasing_across_operations() -> None:
    clock = FakeClock()
    scheduler = PresentationScheduler(clock=clock, neutral_factory=_neutral)
    first = scheduler.submit(_cue("first", lease_s=0.5))
    clock.advance(0.5)
    later = scheduler.tick()

    sequences = [event.sequence for event in (*first.events, *later.events)]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
