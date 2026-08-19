# ADR-005: Semantic avatar control

- Status: Accepted
- Date: 2026-08-19

## Decision

Dialogue emits semantic affect, delivery, gaze, gesture, and posture intent. A deterministic
PerformanceDirector and PresentationScheduler map those intents to renderer capabilities, leases,
priorities, cooldowns, audio timing, cancellation, and neutral fallback.

## Consequence

Models never write frame-level Live2D parameters or hide scene-control commands in spoken text.

