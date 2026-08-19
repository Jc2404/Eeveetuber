# Eeveetuber implementation status

**Updated:** 2026-08-20
**Current milestone:** Phase 1 low-latency dialogue and integrated voice ingress

## Implemented

- Python 3.12 package, `uv.lock`, dependency groups, Ruff, strict mypy, pytest, coverage, and CI.
- Eeveetuber-owned immutable event envelopes with trust, visibility, retention, priority,
  correlation, causation, and session sequence metadata.
- Per-session supervised actors, bounded priority mailboxes, explicit overflow behavior,
  cancellation generations, late-result rejection, and validated interaction states.
- Current-generation session output applies bounded lossless backpressure instead of silently
  rejecting equal-priority audio when full. A replacement generation cancels blocked old output
  promptly, while critical operator/control events retain priority displacement.
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
- Durable-eligible accepted input/output events are journaled sequentially through a bounded
  off-path recorder. Partial ASR hypotheses are deliberately omitted; ephemeral audio bytes are
  redacted while identity, ordering, media, and playback metadata remain.
- Provider-neutral streaming model, speech, and ASR ports, plus provider-neutral PCM/VAD event
  contracts.
- Incremental utterance assembly and a bounded three-stage dialogue pipeline: model generation
  continues while TTS handles an earlier segment, ordered audio remains deterministic, and
  cancellation closes every producer/dispatcher/synthesis task. The application currently permits
  one TTS call at a time; the pipeline preserves ordered output under an explicitly supplied
  bounded parallel-synthesis limit, while adapter capability declaration and environment wiring
  remain TODOs.
- Immutable PCM frame/utterance contracts and a per-instance energy VAD with pre-roll,
  start/end hysteresis, deterministic utterance IDs, and hard duration/byte limits.
- Versioned EVIF v1 inbound PCM WebSocket frames, explicit browser microphone controls, external
  AudioWorklet downmix/resampling, exact-duration mono PCM chunking, and client backpressure limits.
- Per-session bounded VAD-to-ASR coordination with provider-stream validation, stale-utterance
  supersession, ASR deadlines, lossless low-frequency control admission, and speech-onset barge-in.
- Deterministic fake model, speech, ASR, and avatar adapters. The fake ASR retains request metadata,
  never raw PCM.
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
- OpenAI-compatible `/audio/transcriptions` adapter that wraps bounded PCM as WAV entirely in
  memory, supports hosted and keyless-local endpoints, has cancellation/timeout/byte limits, and
  yields one normalized final transcript without persisting audio.
- Environment-backed provider selection with fake adapters as the network-free default, plus
  Ollama and generic OpenAI-compatible example profiles.
- Explicit `reasoning_effort=none` support for low-latency Ollama/Qwen conversation. Python/config
  `None` still means "omit the field," while the enum value sends the literal string `none`.
- Bounded recent user/assistant history for the current session, rendered as data-only,
  non-authoritative context. Message/character/read-deadline limits are validated
  `EEVEETUBER_HISTORY__*` settings (defaults: 12/6000/1500/50 ms); timed-out SQLite workers are
  tracked and drained before shutdown.
- Zero-visible-output completions become a readable, recoverable `turn.failed` event containing
  normalized stop reason and token counts; blank assistant messages are never persisted.
- Opt-in `--verbose` rotating text logs with UTC timestamps, process sequence numbers, event/session
  correlation fields, and recursive secret/conversation/reasoning redaction.
- Session shutdown closes adapter-owned HTTP clients; foreground replacement actively propagates
  its cancellation token into model and speech waits.
- End-to-end fake turn proving context pin → model stream → early segment → fake audio → final plan.
- Barge-in/replacement test proving generation-1 text/audio cannot leak after generation 2 starts.
- Server output and browser playback reject/stop queued stale-generation text and audio immediately
  on cancellation or a newer turn generation.
- Two simultaneous WebSocket sessions with sentinel-data noninterference.
- Five initial ADRs and a direct-source provenance policy.

## Verification baseline

- `613 passed`
- Ruff: clean
- strict mypy: clean across 74 source files
- branch-aware coverage: `86.51%` (required floor: 80%)
- Alembic upgrade from an empty SQLite database: passed
- locked dependency synchronization: passed
- wheel build: passed; packaged wheel contains the operator assets

The current FastAPI TestClient emits one upstream deprecation warning about Starlette's legacy
`httpx` test transport. It does not affect runtime behavior; replace the test transport when the
FastAPI/Starlette ecosystem completes that migration.

## Intentionally not yet implemented

- Selected-image input and Live2D adapters.
- Automated live-provider tests. Model and TTS adapters are contract-tested with deterministic
  HTTP transports; a manual local Qwen3.5/Ollama smoke test passed with reasoning disabled.
- Automated physical-microphone browser tests. EVIF encoding, AudioWorklet resampling/chunking,
  VAD/ASR flow, stale-result rejection, and voice barge-in are deterministic tests; this session
  had no connected in-app browser backend for the final permission-click smoke test.
- ModeCoordinator, automatic conversation/work/game/performance switching, and reasoning routing.
- Background memory candidate extraction/consolidation. The schema and deterministic promotion
  gate exist, but no reflection model is allowed yet.
- Cross-session conversation resume and learned long-term memory injection. Current bounded history
  gives continuity only within the connected session; durable transcripts remain available.
- Skills, SkillLearner, MCP, general tools, approvals, durable work graphs, LangGraph, or RAG.
- Public chat, moderation, game telemetry, OBS, and stream-safe presentation profiles.
- Remote authentication/TLS. The server remains localhost-first.

## Next implementation order

1. Add a Live2D renderer adapter behind the existing semantic performance contracts. If code is
   adapted from Open-LLM-VTuber, add the required file header and third-party notice with its pinned
   path and revision.
2. Connect playback and voice timing to the performance scheduler and expose real adapter
   health/latency, including microphone-to-cancel and speech-end-to-first-audio measurements.
3. Add selected-image input, failure degradation tests, and a bounded soak runner.
4. Only after the realtime vertical slice is measured, implement ModeCoordinator and Phase 2
   local-memory retrieval/consolidation.

## Borrowing status

No implementation source has been copied or adapted from Open-LLM-VTuber, BearCode, or Letta Code
in this milestone. The current code is a clean-room implementation of the documented architecture.
Therefore no source file carries a borrowed-code header yet. The required format is documented in
[docs/PROVENANCE.md](docs/PROVENANCE.md).
