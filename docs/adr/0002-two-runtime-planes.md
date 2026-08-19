# ADR-002: Separate realtime and durable control planes

- Status: Accepted
- Date: 2026-08-19

## Decision

The realtime interaction/media plane and durable cognition/control plane use typed boundaries and
separate scheduling. They may share one process initially.

## Consequence

Audio, cancellation, incremental speech, and avatar timing never wait for reflection, skill learning,
workflow checkpoints, or an auxiliary AI approval service.

