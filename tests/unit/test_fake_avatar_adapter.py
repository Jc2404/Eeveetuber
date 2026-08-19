from eeveetuber.adapters.fake import FakeAvatarAdapter
from eeveetuber.avatar import (
    AvatarCapabilityProfile,
    CuePriority,
    PresentationCue,
    PresentationEvent,
    PresentationEventKind,
    PresentationLayer,
)


async def test_records_resolved_presentation_events() -> None:
    adapter = FakeAvatarAdapter(
        AvatarCapabilityProfile(avatar_id="fake", renderer="fake", capabilities={})
    )
    cue = PresentationCue(
        cue_id="neutral-affect",
        generation=0,
        layer=PresentationLayer.AFFECT,
        semantic_key="affect.neutral",
        adapter_action=None,
        intensity=0.0,
        priority=CuePriority.REACTIVE_IDLE,
        requested_at=1.0,
        not_after=None,
        lease_s=None,
    )
    event = PresentationEvent(
        sequence=0,
        kind=PresentationEventKind.STARTED,
        cue=cue,
        occurred_at=1.0,
    )

    await adapter.dispatch(event)
    await adapter.close()

    assert adapter.events == [event]
    assert adapter.closed
