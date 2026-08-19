# ADR-004: Per-session actor and cancellation generation

- Status: Accepted
- Date: 2026-08-19

## Decision

Each live session owns a bounded priority mailbox and one mutable interaction state. Accepting a
replacement turn advances a monotonic cancellation generation. Every asynchronous result carries its
originating generation and stale results are rejected at the session boundary.

## Consequence

Provider cancellation is an optimization, not the correctness boundary. A provider that finishes late
cannot emit stale speech or avatar cues.

