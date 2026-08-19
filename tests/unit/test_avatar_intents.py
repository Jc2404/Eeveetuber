from dataclasses import fields

import pytest

from eeveetuber.avatar import (
    AffectIntent,
    AffectLabel,
    AvatarCapabilityProfile,
    BlendCurve,
    BlendMode,
    BlendSpec,
    CapabilityBinding,
    CuePriority,
    CueRequest,
    CueSource,
    GazeIntent,
    GazeTarget,
    GestureIntent,
    GestureKind,
    PerformanceDirector,
    PerformanceStyle,
    PostureIntent,
    PostureKind,
    PresentationLayer,
)


def _binding(
    semantic_key: str,
    layer: PresentationLayer,
    action: str | None,
    *,
    resources: frozenset[str] = frozenset(),
    lease_s: float | None = 1.0,
    cooldown_s: float = 0.0,
    max_intensity: float = 1.0,
    blend: BlendSpec | None = None,
) -> CapabilityBinding:
    return CapabilityBinding(
        semantic_key=semantic_key,
        layer=layer,
        adapter_action=action,
        resources=resources,
        default_lease_s=lease_s,
        cooldown_s=cooldown_s,
        max_intensity=max_intensity,
        blend=blend or BlendSpec(),
    )


def _profile() -> AvatarCapabilityProfile:
    neutral = {
        layer: _binding(
            f"{layer.value}.neutral",
            layer,
            f"{layer.value}:neutral",
            resources=frozenset({layer.value}),
            lease_s=None,
        )
        for layer in PresentationLayer
    }
    capabilities = {
        "affect.joy": _binding(
            "affect.joy",
            PresentationLayer.AFFECT,
            "expression:smile",
            resources=frozenset({"face"}),
            lease_s=1.25,
            max_intensity=0.8,
            blend=BlendSpec(
                mode=BlendMode.CROSS_FADE,
                curve=BlendCurve.EASE_OUT,
                fade_in_s=0.2,
                fade_out_s=0.3,
            ),
        ),
        "gesture.wave": _binding(
            "gesture.wave",
            PresentationLayer.GESTURE,
            "motion:wave",
            cooldown_s=2.5,
        ),
        "gaze.user": _binding(
            "gaze.user", PresentationLayer.GAZE, "gaze:camera"
        ),
    }
    return AvatarCapabilityProfile(
        avatar_id="eevee_v1",
        renderer="live2d_web",
        capabilities=capabilities,
        neutral=neutral,
        fallbacks={"gesture.celebrate": "gesture.wave"},
    )


@pytest.mark.parametrize(
    ("intent", "layer", "semantic_key"),
    [
        (AffectIntent(label=AffectLabel.JOY), PresentationLayer.AFFECT, "affect.joy"),
        (GestureIntent(GestureKind.NOD), PresentationLayer.GESTURE, "gesture.nod"),
        (GazeIntent(GazeTarget.USER), PresentationLayer.GAZE, "gaze.user"),
        (PostureIntent(PostureKind.ATTENTIVE), PresentationLayer.POSTURE, "posture.attentive"),
    ],
)
def test_semantic_intents_have_stable_namespaces(intent, layer, semantic_key) -> None:
    assert intent.layer is layer
    assert intent.semantic_key == semantic_key


@pytest.mark.parametrize(
    ("intent", "label"),
    [
        (AffectIntent(valence=0.4, arousal=0.2), AffectLabel.JOY),
        (AffectIntent(valence=0.4, arousal=0.9), AffectLabel.EXCITEMENT),
        (AffectIntent(valence=-0.4, arousal=0.2), AffectLabel.SADNESS),
        (AffectIntent(valence=-0.4, arousal=0.8), AffectLabel.ANGER),
        (AffectIntent(valence=0.0, arousal=0.9), AffectLabel.SURPRISE),
        (AffectIntent(), AffectLabel.NEUTRAL),
    ],
)
def test_unlabelled_affect_has_deterministic_mapping(intent: AffectIntent, label: AffectLabel) -> None:
    assert intent.effective_label is label


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AffectIntent(valence=1.1),
        lambda: AffectIntent(arousal=float("nan")),
        lambda: GestureIntent(GestureKind.WAVE, intensity=-0.1),
        lambda: GazeIntent(GazeTarget.USER, intensity=float("inf")),
        lambda: PostureIntent(PostureKind.OPEN, intensity=1.01),
    ],
)
def test_intents_reject_out_of_range_or_non_finite_values(factory) -> None:
    with pytest.raises(ValueError):
        factory()


def test_cognition_contracts_do_not_expose_renderer_controls() -> None:
    forbidden_fragments = {"parameter", "cubism", "motion_index", "javascript", "frame"}
    for intent_type in (AffectIntent, GestureIntent, GazeIntent, PostureIntent):
        field_names = {item.name.lower() for item in fields(intent_type)}
        assert all(
            fragment not in name
            for fragment in forbidden_fragments
            for name in field_names
        )


def test_capability_profile_resolves_exact_configured_and_neutral_fallbacks() -> None:
    profile = _profile()

    exact = profile.resolve(AffectIntent(label=AffectLabel.JOY))
    configured = profile.resolve(GestureIntent(GestureKind.CELEBRATE))
    missing = profile.resolve(PostureIntent(PostureKind.LEAN_FORWARD))

    assert exact.resolved_key == "affect.joy"
    assert not exact.used_fallback
    assert configured.resolved_key == "gesture.wave"
    assert configured.reason == "configured_fallback"
    assert missing.resolved_key == "posture.neutral"
    assert missing.reason == "neutral_fallback"


def test_profile_without_neutral_art_uses_safe_noop() -> None:
    profile = AvatarCapabilityProfile(
        avatar_id="minimal",
        renderer="fake",
        capabilities={},
    )

    resolution = profile.resolve(GazeIntent(GazeTarget.USER))

    assert resolution.resolved_key == "gaze.neutral"
    assert resolution.binding.adapter_action is None
    assert resolution.reason == "neutral_noop"


def test_profile_mappings_are_immutable_and_stably_ordered() -> None:
    profile = _profile()

    assert profile.semantic_keys == ("affect.joy", "gaze.user", "gesture.wave")
    with pytest.raises(TypeError):
        profile.capabilities["gesture.nod"] = _binding(  # type: ignore[index]
            "gesture.nod", PresentationLayer.GESTURE, "motion:nod"
        )


def test_profile_rejects_cross_layer_fallback_and_cycles() -> None:
    with pytest.raises(ValueError, match="cross"):
        AvatarCapabilityProfile(
            avatar_id="bad",
            renderer="fake",
            capabilities={},
            fallbacks={"gesture.nod": "affect.joy"},
        )
    with pytest.raises(ValueError, match="cycle"):
        AvatarCapabilityProfile(
            avatar_id="bad",
            renderer="fake",
            capabilities={},
            fallbacks={"gesture.nod": "gesture.wave", "gesture.wave": "gesture.nod"},
        )


def test_director_applies_profile_limits_style_and_blend_metadata() -> None:
    director = PerformanceDirector(
        _profile(),
        PerformanceStyle(
            intensity_scale={PresentationLayer.AFFECT: 1.5},
            lease_scale={PresentationLayer.AFFECT: 2.0},
        ),
    )
    request = CueRequest(
        cue_id="joy-1",
        generation=3,
        intent=AffectIntent(label=AffectLabel.JOY, intensity=0.7),
        requested_at=10.0,
        source=CueSource.INTERACTION_STATE,
        ttl_s=0.5,
    )

    cue = director.resolve(request)

    assert cue.adapter_action == "expression:smile"
    assert cue.intensity == 0.8
    assert cue.priority is CuePriority.INTERACTION_STATE
    assert cue.not_after == 10.5
    assert cue.lease_s == 2.5
    assert cue.resources == frozenset({"face"})
    assert cue.blend.curve is BlendCurve.EASE_OUT
    assert cue.fallback_from is None


def test_director_marks_missing_and_style_suppressed_cues_as_neutral() -> None:
    profile = _profile()
    director = PerformanceDirector(
        profile,
        PerformanceStyle(suppressed_semantics=frozenset({"gesture.wave"})),
    )
    missing = director.resolve(
        CueRequest("missing", 0, GestureIntent(GestureKind.NOD), requested_at=0.0)
    )
    suppressed = director.resolve(
        CueRequest("quiet", 0, GestureIntent(GestureKind.WAVE), requested_at=0.0)
    )

    assert missing.neutral
    assert missing.semantic_key == "gesture.neutral"
    assert missing.fallback_from == "gesture.nod"
    assert suppressed.neutral
    assert suppressed.fallback_from == "gesture.wave"


def test_operator_and_speech_sync_cues_are_limit_exempt() -> None:
    director = PerformanceDirector(_profile())
    for source in (CueSource.OPERATOR, CueSource.SPEECH_SYNC):
        cue = director.resolve(
            CueRequest(
                cue_id=source.value,
                generation=0,
                intent=GazeIntent(GazeTarget.USER),
                requested_at=0.0,
                source=source,
            )
        )
        assert cue.limits_exempt


def test_neutral_factory_is_indefinite_and_generation_scoped() -> None:
    cue = PerformanceDirector(_profile()).neutral_cue(
        PresentationLayer.AFFECT,
        generation=7,
        requested_at=12.0,
    )

    assert cue.cue_id == "neutral:affect:7"
    assert cue.neutral
    assert cue.lease_s is None
    assert cue.not_after is None
    assert cue.generation == 7
