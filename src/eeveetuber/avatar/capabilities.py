"""Avatar capability declarations and deterministic semantic fallback."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .intents import PresentationLayer, SemanticIntent
from .presentation import BlendSpec


def _validate_semantic_key(key: str, layer: PresentationLayer) -> None:
    if not key or key.strip() != key:
        raise ValueError("semantic keys must be non-empty and trimmed")
    if not key.startswith(f"{layer.value}.") or key.endswith("."):
        raise ValueError(f"semantic key {key!r} does not belong to {layer.value!r}")


@dataclass(frozen=True, slots=True)
class CapabilityBinding:
    """Avatar-side mapping from one semantic key to an opaque adapter action.

    ``adapter_action`` is defined by the renderer adapter/profile and never
    appears in cognition contracts.  ``resources`` names scheduler-level locks,
    not raw Cubism parameters.
    """

    semantic_key: str
    layer: PresentationLayer
    adapter_action: str | None
    resources: frozenset[str] = field(default_factory=frozenset)
    default_lease_s: float | None = 1.5
    cooldown_s: float = 0.0
    cooldown_key: str | None = None
    max_intensity: float = 1.0
    blend: BlendSpec = field(default_factory=BlendSpec)

    def __post_init__(self) -> None:
        _validate_semantic_key(self.semantic_key, self.layer)
        if self.adapter_action is not None and (
            not self.adapter_action or self.adapter_action.strip() != self.adapter_action
        ):
            raise ValueError("adapter_action must be non-empty and trimmed when provided")
        for resource in self.resources:
            if not resource or resource.strip() != resource:
                raise ValueError("resource names must be non-empty and trimmed")
        if self.default_lease_s is not None and (
            not math.isfinite(self.default_lease_s) or self.default_lease_s <= 0.0
        ):
            raise ValueError("default_lease_s must be finite and greater than zero")
        if not math.isfinite(self.cooldown_s) or self.cooldown_s < 0.0:
            raise ValueError("cooldown_s must be finite and non-negative")
        if self.cooldown_key is not None and (
            not self.cooldown_key or self.cooldown_key.strip() != self.cooldown_key
        ):
            raise ValueError("cooldown_key must be non-empty and trimmed when provided")
        if not math.isfinite(self.max_intensity) or not 0.0 <= self.max_intensity <= 1.0:
            raise ValueError("max_intensity must be finite and between 0.0 and 1.0")

    @classmethod
    def neutral_noop(cls, layer: PresentationLayer) -> CapabilityBinding:
        """A safe renderer-independent fallback when a profile lacks neutral art."""

        return cls(
            semantic_key=f"{layer.value}.neutral",
            layer=layer,
            adapter_action=None,
            default_lease_s=None,
            max_intensity=0.0,
        )


@dataclass(frozen=True, slots=True)
class CapabilityResolution:
    requested_key: str
    resolved_key: str
    binding: CapabilityBinding
    used_fallback: bool
    reason: str


@dataclass(frozen=True, slots=True)
class AvatarCapabilityProfile:
    """Immutable model-specific avatar capabilities.

    Configured fallback chains must stay within a presentation layer and must be
    acyclic.  A missing semantic always resolves to the layer's declared neutral
    binding, or to a safe no-op neutral binding when the profile omits one.
    """

    avatar_id: str
    renderer: str
    capabilities: Mapping[str, CapabilityBinding]
    neutral: Mapping[PresentationLayer, CapabilityBinding] = field(default_factory=dict)
    fallbacks: Mapping[str, str] = field(default_factory=dict)
    api_version: int = 1

    def __post_init__(self) -> None:
        if not self.avatar_id or self.avatar_id.strip() != self.avatar_id:
            raise ValueError("avatar_id must be non-empty and trimmed")
        if not self.renderer or self.renderer.strip() != self.renderer:
            raise ValueError("renderer must be non-empty and trimmed")
        if self.api_version < 1:
            raise ValueError("api_version must be positive")

        capabilities = dict(sorted(self.capabilities.items()))
        for key, binding in capabilities.items():
            if key != binding.semantic_key:
                raise ValueError(
                    f"capability key {key!r} does not match binding key {binding.semantic_key!r}"
                )

        neutral = dict(sorted(self.neutral.items(), key=lambda item: item[0].value))
        for layer, binding in neutral.items():
            if binding.layer is not layer:
                raise ValueError(f"neutral binding for {layer.value!r} belongs to another layer")

        fallbacks = dict(sorted(self.fallbacks.items()))
        for source, target in fallbacks.items():
            source_layer = self._layer_from_key(source)
            target_layer = self._layer_from_key(target)
            if source_layer is not target_layer:
                raise ValueError("fallbacks cannot cross presentation layers")
        self._validate_no_fallback_cycles(fallbacks)

        object.__setattr__(self, "capabilities", MappingProxyType(capabilities))
        object.__setattr__(self, "neutral", MappingProxyType(neutral))
        object.__setattr__(self, "fallbacks", MappingProxyType(fallbacks))

    @staticmethod
    def _layer_from_key(key: str) -> PresentationLayer:
        if not key or key.strip() != key or "." not in key:
            raise ValueError(f"invalid semantic key {key!r}")
        namespace = key.partition(".")[0]
        try:
            return PresentationLayer(namespace)
        except ValueError as error:
            raise ValueError(f"unknown semantic namespace {namespace!r}") from error

    @staticmethod
    def _validate_no_fallback_cycles(fallbacks: Mapping[str, str]) -> None:
        for origin in fallbacks:
            seen: set[str] = set()
            current = origin
            while current in fallbacks:
                if current in seen:
                    raise ValueError(f"fallback cycle contains {current!r}")
                seen.add(current)
                current = fallbacks[current]

    @property
    def semantic_keys(self) -> tuple[str, ...]:
        """Stable capability order for introspection, snapshots, and tests."""

        return tuple(self.capabilities)

    def neutral_binding(self, layer: PresentationLayer) -> CapabilityBinding:
        return self.neutral.get(layer, CapabilityBinding.neutral_noop(layer))

    def resolve(self, intent: SemanticIntent) -> CapabilityResolution:
        requested_key = intent.semantic_key
        layer = intent.layer
        _validate_semantic_key(requested_key, layer)

        current = requested_key
        visited: set[str] = set()
        while current not in visited:
            visited.add(current)
            binding = self.capabilities.get(current)
            if binding is not None:
                return CapabilityResolution(
                    requested_key=requested_key,
                    resolved_key=current,
                    binding=binding,
                    used_fallback=current != requested_key,
                    reason="exact" if current == requested_key else "configured_fallback",
                )
            target = self.fallbacks.get(current)
            if target is None:
                break
            current = target

        binding = self.neutral_binding(layer)
        return CapabilityResolution(
            requested_key=requested_key,
            resolved_key=binding.semantic_key,
            binding=binding,
            used_fallback=binding.semantic_key != requested_key,
            reason="neutral_fallback" if binding.adapter_action is not None else "neutral_noop",
        )


__all__ = [
    "AvatarCapabilityProfile",
    "CapabilityBinding",
    "CapabilityResolution",
]
