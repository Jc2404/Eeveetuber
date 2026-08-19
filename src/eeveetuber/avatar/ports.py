"""Framework-independent ports at the avatar renderer boundary."""

from __future__ import annotations

from typing import Protocol

from .capabilities import AvatarCapabilityProfile
from .presentation import PresentationEvent


class AvatarAdapter(Protocol):
    """Consume scheduler events using one adapter-owned rendering implementation.

    Implementations may translate an event's opaque ``adapter_action`` into
    Live2D, VTube Studio, VRM, or another renderer's native commands.  They must
    not interpret cognition output directly.
    """

    @property
    def profile(self) -> AvatarCapabilityProfile: ...

    async def dispatch(self, event: PresentationEvent) -> None: ...

    async def close(self) -> None: ...


class PresentationEventSink(Protocol):
    """Optional fan-out/observability sink for resolved scheduler events."""

    async def publish(self, event: PresentationEvent) -> None: ...


__all__ = ["AvatarAdapter", "PresentationEventSink"]
