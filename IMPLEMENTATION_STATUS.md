# Eeveetuber implementation status

**Updated:** 2026-08-19  
**Current milestone:** Phase 0 backbone plus deterministic fake Phase 1 vertical tracer

## Implemented

- Python 3.12 package, `uv.lock`, dependency groups, Ruff, strict mypy, pytest, coverage, and CI.
- Eeveetuber-owned immutable event envelopes with trust, visibility, retention, priority,
  correlation, causation, and session sequence metadata.
- Per-session supervised actors, bounded priority mailboxes, explicit overflow behavior,
  cancellation generations, late-result rejection, and validated interaction states.
- Generation-gated state transitions for background/spawned model and TTS work.
- Versioned owner-authored character profiles.
- T0/T1/T2 context compilation with deterministic budgets, demotion/trimming, minimal fallback,
  immutable revision pins, hot cache publication, and off-path snapshot persistence.
- Typed memory candidates, provenance, trust, scope, consent, confidence, sensitivity,
  visibility, retention, deterministic promotion decisions, transactional revisions, and rollback
  foundations.
- SQLite WAL storage for sessions, messages, events, checkpoints, outbox items, memory, context
  snapshots, FTS5 indexes, and stable-ID “needle then expand” transcript recall.
- Alembic initial migration and environment-selectable database URL.
- Provider-neutral streaming model and speech ports.
- Incremental utterance assembly: validated sentence segments reach TTS before the complete model
  response and are collected into a final replayable plan.
- Deterministic fake model, speech, and avatar adapters.
- Renderer-independent affect, gesture, gaze, and posture intents; immutable avatar capability
  profiles; semantic fallback; performance director; presentation scheduler with priorities,
  leases, resources, cooldowns, rate limits, audio markers, cancellation generations, and neutral
  recovery.
- FastAPI health endpoint and versioned `/v1/ws` text-turn/cancel/operator/ping protocol.
- End-to-end fake turn proving context pin → model stream → early segment → fake audio → final plan.
- Barge-in/replacement test proving generation-1 text/audio cannot leak after generation 2 starts.
- Two simultaneous WebSocket sessions with sentinel-data noninterference.
- Five initial ADRs and a direct-source provenance policy.

## Verification baseline

- `275 passed`
- Ruff: clean
- strict mypy: clean across 56 source files
- branch-aware coverage: `86.10%` (required floor: 80%)
- Alembic upgrade from an empty SQLite database: passed
- locked dependency synchronization: passed

The current FastAPI TestClient emits one upstream deprecation warning about Starlette's legacy
`httpx` test transport. It does not affect runtime behavior; replace the test transport when the
FastAPI/Starlette ecosystem completes that migration.

## Intentionally not yet implemented

- Production VAD/ASR, model, streaming TTS, audio playback, or Live2D adapters.
- Browser/operator UI and binary WebSocket audio framing.
- ModeCoordinator, automatic conversation/work/game/performance switching, and reasoning routing.
- Background memory candidate extraction/consolidation. The schema and deterministic promotion
  gate exist, but no reflection model is allowed yet.
- Skills, SkillLearner, MCP, general tools, approvals, durable work graphs, LangGraph, or RAG.
- Public chat, moderation, game telemetry, OBS, and stream-safe presentation profiles.
- Remote authentication/TLS. The server remains localhost-first.

## Next implementation order

1. Freeze the binary audio and status portions of WebSocket protocol v1 and add a minimal operator
   client that renders the existing fake trace.
2. Add one OpenAI-compatible model adapter with normalized stream/cancellation/capability contract
   tests, plus one local adapter profile.
3. Add one streaming TTS adapter and deterministic audio framing/playback acknowledgements.
4. Add per-session VAD and streaming/final ASR ports, then prove measured barge-in timing.
5. Add a Live2D renderer adapter behind the existing semantic performance contracts. If code is
   adapted from Open-LLM-VTuber, add the required file header and third-party notice with its pinned
   path and revision.
6. Add the browser/operator controls for mute, stop speech, neutral avatar, and kill session.
7. Only after the realtime vertical slice is measured, implement ModeCoordinator and Phase 2
   local-memory retrieval/consolidation.

## Borrowing status

No implementation source has been copied or adapted from Open-LLM-VTuber, BearCode, or Letta Code
in this milestone. The current code is a clean-room implementation of the documented architecture.
Therefore no source file carries a borrowed-code header yet. The required format is documented in
[docs/PROVENANCE.md](docs/PROVENANCE.md).
