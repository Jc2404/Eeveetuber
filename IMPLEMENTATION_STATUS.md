# Eeveetuber implementation status

**Updated:** 2026-08-20
**Current milestone:** Phase 1 conversation reliability, observability, and provider slice

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
- Accepted input/output events are journaled sequentially through a bounded off-path recorder;
  ephemeral audio bytes are redacted while identity, ordering, media, and playback metadata remain.
- Provider-neutral streaming model and speech ports.
- Incremental utterance assembly: validated sentence segments reach TTS before the complete model
  response and are collected into a final replayable plan.
- Deterministic fake model, speech, and avatar adapters.
- Renderer-independent affect, gesture, gaze, and posture intents; immutable avatar capability
  profiles; semantic fallback; performance director; presentation scheduler with priorities,
  leases, resources, cooldowns, rate limits, audio markers, cancellation generations, and neutral
  recovery.
- FastAPI health endpoint and versioned `/v1/ws` text-turn/cancel/operator/ping protocol.
- Dependency-free operator console mounted at `/`, with text/history/event views and
  stop-speech, mute, neutral-avatar, and kill-session controls.
- Negotiated `eeveetuber.v1.binary-audio` WebSocket subprotocol and strictly validated EVAF v1
  frames carrying correlation, generation, sequence, media, and audio metadata. Legacy clients
  retain JSON/base64 audio.
- Ordered browser segment buffering and playback acknowledgements for every constituent audio
  chunk; stale-generation acknowledgements are rejected by the session actor.
- OpenAI-compatible Chat Completions SSE adapter with hosted/keyless-local configuration,
  cancellation-aware reads, bounded parsing/errors, normalized finish reasons and usage, and
  guaranteed response cleanup.
- OpenAI-compatible `/audio/speech` streaming adapter with hosted/keyless-local configuration,
  bounded ordered chunks, cancellation, timeout/byte limits, deterministic media metadata, and
  guaranteed response cleanup.
- Environment-backed provider selection with fake adapters as the network-free default, plus
  Ollama and generic OpenAI-compatible example profiles.
- Explicit `reasoning_effort=none` support for low-latency Ollama/Qwen conversation. Python/config
  `None` still means "omit the field," while the enum value sends the literal string `none`.
- Bounded recent user/assistant history for the current session, loaded from SQLite under a 50 ms
  local deadline and rendered as data-only, non-authoritative context. Timed-out SQLite workers are
  tracked and drained before shutdown.
- Zero-visible-output completions become a readable, recoverable `turn.failed` event containing
  normalized stop reason and token counts; blank assistant messages are never persisted.
- Opt-in `--verbose` rotating text logs with UTC timestamps, process sequence numbers, event/session
  correlation fields, and recursive secret/conversation/reasoning redaction.
- Session shutdown closes adapter-owned HTTP clients; foreground replacement actively propagates
  its cancellation token into model and speech waits.
- End-to-end fake turn proving context pin → model stream → early segment → fake audio → final plan.
- Barge-in/replacement test proving generation-1 text/audio cannot leak after generation 2 starts.
- Two simultaneous WebSocket sessions with sentinel-data noninterference.
- Five initial ADRs and a direct-source provenance policy.

## Verification baseline

- `415 passed`
- Ruff: clean
- strict mypy: clean across 65 source files
- branch-aware coverage: `86.42%` (required floor: 80%)
- Alembic upgrade from an empty SQLite database: passed
- locked dependency synchronization: passed
- wheel build: passed; packaged wheel contains the operator assets

The current FastAPI TestClient emits one upstream deprecation warning about Starlette's legacy
`httpx` test transport. It does not affect runtime behavior; replace the test transport when the
FastAPI/Starlette ecosystem completes that migration.

## Intentionally not yet implemented

- Production VAD/ASR, microphone input, selected-image input, or Live2D adapters.
- Automated live-provider tests. Model and TTS adapters are contract-tested with deterministic
  HTTP transports; a manual local Qwen3.5/Ollama smoke test passed with reasoning disabled.
- ModeCoordinator, automatic conversation/work/game/performance switching, and reasoning routing.
- Background memory candidate extraction/consolidation. The schema and deterministic promotion
  gate exist, but no reflection model is allowed yet.
- Cross-session conversation resume and learned long-term memory injection. Current bounded history
  gives continuity only within the connected session; durable transcripts remain available.
- Skills, SkillLearner, MCP, general tools, approvals, durable work graphs, LangGraph, or RAG.
- Public chat, moderation, game telemetry, OBS, and stream-safe presentation profiles.
- Remote authentication/TLS. The server remains localhost-first.

## Next implementation order

1. Add per-session microphone framing, VAD and streaming/final ASR ports, then prove measured
   barge-in timing.
2. Add a Live2D renderer adapter behind the existing semantic performance contracts. If code is
   adapted from Open-LLM-VTuber, add the required file header and third-party notice with its pinned
   path and revision.
3. Connect playback timing to the performance scheduler and expose real adapter health/latency.
4. Add selected-image input, failure degradation tests, and a bounded soak runner.
5. Only after the realtime vertical slice is measured, implement ModeCoordinator and Phase 2
   local-memory retrieval/consolidation.

## Borrowing status

No implementation source has been copied or adapted from Open-LLM-VTuber, BearCode, or Letta Code
in this milestone. The current code is a clean-room implementation of the documented architecture.
Therefore no source file carries a borrowed-code header yet. The required format is documented in
[docs/PROVENANCE.md](docs/PROVENANCE.md).
