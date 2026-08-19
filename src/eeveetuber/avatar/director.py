"""Semantic-to-capability translation for avatar presentation."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .capabilities import AvatarCapabilityProfile, CapabilityBinding, CapabilityResolution
from .intents import PresentationLayer
from .presentation import (
    CuePriority,
    CueRequest,
    CueSource,
    PresentationCue,
)


@dataclass(frozen=True, slots=True)
class PerformanceStyle:
    """Deterministic persona styling applied after semantic interpretation."""

    intensity_scale: Mapping[PresentationLayer, float] = field(default_factory=dict)
    lease_scale: Mapping[PresentationLayer, float] = field(default_factory=dict)
    suppressed_semantics: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        intensity_scale = dict(sorted(self.intensity_scale.items(), key=lambda item: item[0].value))
        lease_scale = dict(sorted(self.lease_scale.items(), key=lambda item: item[0].value))
        for scale in intensity_scale.values():
            if not math.isfinite(scale) or scale < 0.0:
                raise ValueError("intensity scales must be finite and non-negative")
        for scale in lease_scale.values():
            if not math.isfinite(scale) or scale <= 0.0:
                raise ValueError("lease scales must be finite and greater than zero")
        for semantic_key in self.suppressed_semantics:
            if not semantic_key or semantic_key.strip() != semantic_key:
                raise ValueError("suppressed semantic keys must be non-empty and trimmed")
        object.__setattr__(self, "intensity_scale", MappingProxyType(intensity_scale))
        object.__setattr__(self, "lease_scale", MappingProxyType(lease_scale))


class PerformanceDirector:
    """Stateless mapper from host-enveloped semantic requests to presentation cues."""

    def __init__(
        self,
        profile: AvatarCapabilityProfile,
        style: PerformanceStyle | None = None,
    ) -> None:
        self._profile = profile
        self._style = style or PerformanceStyle()

    @property
    def profile(self) -> AvatarCapabilityProfile:
        return self._profile

    @property
    def style(self) -> PerformanceStyle:
        return self._style

    def resolve(self, request: CueRequest) -> PresentationCue:
        resolution = self._profile.resolve(request.intent)
        if request.intent.semantic_key in self._style.suppressed_semantics:
            neutral = self._profile.neutral_binding(request.intent.layer)
            resolution = CapabilityResolution(
                requested_key=request.intent.semantic_key,
                resolved_key=neutral.semantic_key,
                binding=neutral,
                used_fallback=True,
                reason="style_suppressed",
            )
        return self._make_cue(request, resolution)

    def resolve_many(self, requests: Iterable[CueRequest]) -> tuple[PresentationCue, ...]:
        """Resolve in caller order so streamed segment replay stays deterministic."""

        return tuple(self.resolve(request) for request in requests)

    def _make_cue(
        self,
        request: CueRequest,
        resolution: CapabilityResolution,
    ) -> PresentationCue:
        binding = resolution.binding
        layer = request.intent.layer
        intensity_scale = self._style.intensity_scale.get(layer, 1.0)
        intensity = min(request.intent.intensity * intensity_scale, binding.max_intensity)
        neutral_binding = self._profile.neutral_binding(layer)
        is_neutral = binding == neutral_binding

        if request.lease_s is not None:
            lease_s = request.lease_s
        elif binding.default_lease_s is None:
            lease_s = None
        else:
            lease_s = binding.default_lease_s * self._style.lease_scale.get(layer, 1.0)

        limits_exempt = request.limits_exempt or request.source in {
            CueSource.OPERATOR,
            CueSource.SPEECH_SYNC,
        }
        return PresentationCue(
            cue_id=request.cue_id,
            generation=request.generation,
            layer=layer,
            semantic_key=resolution.resolved_key,
            adapter_action=binding.adapter_action,
            intensity=intensity,
            priority=request.effective_priority,
            requested_at=request.requested_at,
            not_after=request.not_after,
            lease_s=lease_s,
            resources=binding.resources,
            blend=binding.blend,
            cooldown_key=binding.cooldown_key or binding.semantic_key,
            cooldown_s=0.0 if is_neutral else binding.cooldown_s,
            audio_binding=request.audio_binding,
            source=request.source,
            neutral=is_neutral,
            limits_exempt=limits_exempt or is_neutral,
            fallback_from=(resolution.requested_key if resolution.used_fallback else None),
        )

    def neutral_cue(
        self,
        layer: PresentationLayer,
        generation: int,
        requested_at: float,
    ) -> PresentationCue:
        """Build an indefinite safe baseline used only by scheduler recovery."""

        binding: CapabilityBinding = self._profile.neutral_binding(layer)
        return PresentationCue(
            cue_id=f"neutral:{layer.value}:{generation}",
            generation=generation,
            layer=layer,
            semantic_key=binding.semantic_key,
            adapter_action=binding.adapter_action,
            intensity=binding.max_intensity,
            priority=CuePriority.REACTIVE_IDLE,
            requested_at=requested_at,
            not_after=None,
            lease_s=None,
            resources=binding.resources,
            blend=binding.blend,
            source=CueSource.REACTIVE_IDLE,
            neutral=True,
            limits_exempt=True,
        )


__all__ = ["PerformanceDirector", "PerformanceStyle"]
