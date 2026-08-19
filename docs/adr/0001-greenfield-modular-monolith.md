# ADR-001: Greenfield modular monolith

- Status: Accepted
- Date: 2026-08-19

## Decision

Build a new Python modular monolith. Port selected provider/media adapters only after they conform to
Eeveetuber contracts. Do not fork Open-LLM-VTuber, BearCode, or Letta Code.

## Rationale

The references contain useful integrations and patterns, but none supplies the required combination
of realtime cancellation, memory authority, avatar scheduling, and durable background work.

