"""Deterministic avatar sink for contract tests and replay traces."""

from __future__ import annotations

from eeveetuber.avatar import AvatarCapabilityProfile, PresentationEvent


class FakeAvatarAdapter:
    def __init__(self, profile: AvatarCapabilityProfile) -> None:
        self._profile = profile
        self.events: list[PresentationEvent] = []
        self.closed = False

    @property
    def profile(self) -> AvatarCapabilityProfile:
        return self._profile

    async def dispatch(self, event: PresentationEvent) -> None:
        if self.closed:
            raise RuntimeError("fake avatar adapter is closed")
        self.events.append(event)

    async def close(self) -> None:
        self.closed = True

