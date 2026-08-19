# Eeveetuber

Eeveetuber is a low-latency, memory-first VTuber agent runtime. It combines a responsive
conversation/media plane with a separate durable work and memory plane.

The implementation is being built from the architecture and requirements in
[PROJECT_ARCHITECTURE_AND_REQUIREMENTS.md](PROJECT_ARCHITECTURE_AND_REQUIREMENTS.md).
Current completion and verification results are tracked in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## Current milestone

The Phase 1 provider and operator slice now provides:

- versioned typed events and per-session isolation;
- bounded actor mailboxes and cancellation generations;
- a cached, revision-pinned context snapshot;
- a bounded model-to-segment-to-TTS pipeline that continues model generation while an earlier
  segment is synthesized, while preserving output order;
- semantic avatar cues and deterministic arbitration;
- SQLite-backed transcript/event persistence;
- a versioned FastAPI/WebSocket vertical tracer with deterministic fake adapters;
- a dependency-free operator console at `http://127.0.0.1:12393/`;
- negotiated EVAF v1 binary audio frames, ordered browser playback, and correlated playback
  acknowledgements;
- configurable OpenAI-compatible streaming model and speech adapters, including keyless local
  model endpoints such as Ollama's OpenAI-compatible route;
- explicit browser microphone capture, versioned bounded PCM framing, per-session VAD, barge-in,
  and configurable fake or OpenAI-compatible ASR;
- bounded, deadline-limited recent conversation context within each connected session;
- off-path SQLite event journaling with audio bytes redacted and partial ASR hypotheses omitted,
  plus explicit diagnostics when a model consumes its budget without emitting visible text.

The zero-configuration default remains fake model, fake speech, and fake ASR. Live2D,
selected-image input, and public-chat adapters are not implemented yet.

## Run the operator tracer

```powershell
uv run eeveetuber
```

Open `http://127.0.0.1:12393/`, connect, and send a text turn. With the default configuration the
model echoes the message and speech is deliberately fake, so the browser will report that the
fake payload cannot be decoded while still exercising the complete transport and acknowledgement path.

### Optional verbose file log

Normal runs log only to the console. Add `--verbose` when diagnosing a local run:

```powershell
uv run eeveetuber --verbose
```

This adds a rotating, human-readable DEBUG log at `<data directory>/logs/eeveetuber.log`. Each
line starts with a UTC timestamp and process-local sequence such as `#000123`. Structured secret,
conversation-text, and private-reasoning fields are redacted; transcripts and durable events remain
in their dedicated stores rather than the diagnostic log.

The location and rotation can be changed with `EEVEETUBER_LOG_DIR`,
`EEVEETUBER_LOG_FILENAME`, `EEVEETUBER_LOG_MAX_BYTES`, and
`EEVEETUBER_LOG_BACKUP_COUNT`. The log directory is not created unless `--verbose` is enabled.

## Configure a real model, speech, or ASR endpoint

Settings use the `EEVEETUBER_` prefix and `__` for nested fields. For model and TTS settings, copy
the relevant lines from `profiles/providers/openai-compatible.env.example` or
`profiles/providers/ollama.env.example` into a local `.env` file. For example:

```dotenv
EEVEETUBER_MODEL__PROVIDER=openai_compatible
EEVEETUBER_MODEL__BASE_URL=http://127.0.0.1:11434/v1
EEVEETUBER_MODEL__MODEL=replace-with-your-local-model
EEVEETUBER_MODEL__REASONING_EFFORT=none
EEVEETUBER_MODEL__MAX_OUTPUT_TOKENS=512
```

Omit the API key for a trusted local endpoint. Keep `.env` private; it is excluded from Git.
For reasoning-capable Ollama chat models, the literal value `none` disables hidden reasoning on
the low-latency conversation path. Omitting the setting is different: Eeveetuber then omits the
request field and the provider chooses its default.

ASR is also provider-configurable. A keyless local transcription endpoint can be prepared as:

```dotenv
EEVEETUBER_ASR__PROVIDER=openai_compatible
EEVEETUBER_ASR__BASE_URL=http://127.0.0.1:9000/v1
EEVEETUBER_ASR__MODEL=whisper-local
EEVEETUBER_ASR__LANGUAGE=en
# EEVEETUBER_ASR__API_KEY=replace-only-for-authenticated-endpoints
# EEVEETUBER_ASR__PROMPT=Eevee, VTuber
# EEVEETUBER_ASR__TEMPERATURE=0
EEVEETUBER_ASR__REQUEST_TIMEOUT_SECONDS=30
EEVEETUBER_ASR__CONNECT_TIMEOUT_SECONDS=5
EEVEETUBER_ASR__MAX_INPUT_PCM_BYTES=67108864
EEVEETUBER_ASR__MAX_RESPONSE_BYTES=1048576
```

Browser capture, VAD, bounded ASR coordination, and barge-in use validated settings as well:

```dotenv
EEVEETUBER_VOICE__ENABLED=true
EEVEETUBER_VOICE__SAMPLE_RATE_HZ=16000
EEVEETUBER_VOICE__CHANNELS=1
EEVEETUBER_VOICE__FRAME_DURATION_MS=20
EEVEETUBER_VOICE__MAX_FRAME_BYTES=8192
EEVEETUBER_VOICE__SPEECH_START_THRESHOLD=1200
EEVEETUBER_VOICE__SPEECH_END_THRESHOLD=700
EEVEETUBER_VOICE__SPEECH_START_FRAMES=2
EEVEETUBER_VOICE__SPEECH_END_FRAMES=5
EEVEETUBER_VOICE__PRE_ROLL_FRAMES=5
EEVEETUBER_VOICE__MAX_UTTERANCE_DURATION_MS=30000
EEVEETUBER_VOICE__MAX_UTTERANCE_BYTES=1048576
EEVEETUBER_VOICE__ASR_TIMEOUT_MS=30000
EEVEETUBER_VOICE__MAX_PENDING_UTTERANCES=2
EEVEETUBER_VOICE__MAX_TRANSCRIPT_CHARS=32000
EEVEETUBER_VOICE__BARGE_IN_ENABLED=true
```

The configured frame duration must resolve to a whole PCM sample count. The byte and duration
limits must also be large enough to reach the configured speech-start hysteresis, and the voice
utterance byte limit cannot exceed `EEVEETUBER_ASR__MAX_INPUT_PCM_BYTES`. Dialogue-pipeline tuning
remains on conservative code defaults; planned `EEVEETUBER_DIALOGUE__*` settings will be exposed
after measured latency and backpressure profiles exist.

After connecting the operator console, press **Start microphone** to grant access explicitly. With
the default fake ASR, any detected utterance becomes the deterministic text “Hello from fake ASR.”;
this verifies capture, VAD, framing, barge-in, and dialogue without a network service. Configure a
real ASR endpoint to receive actual transcripts.

Recent current-session history uses the following defaults, all of which can be overridden in
`.env` without editing code:

```dotenv
EEVEETUBER_HISTORY__MAX_MESSAGES=12
EEVEETUBER_HISTORY__MAX_CHARS=6000
EEVEETUBER_HISTORY__MAX_MESSAGE_CHARS=1500
EEVEETUBER_HISTORY__LOAD_TIMEOUT_MS=50
```

`MAX_MESSAGES` counts individual user or assistant messages, rather than complete exchanges.
Setting `MAX_MESSAGES`, `MAX_CHARS`, or `LOAD_TIMEOUT_MS` to `0` disables recent-history loading.
Keep the deadline small on the realtime path; the load falls back to no recent history instead of
delaying a reply.

Session messages, context snapshots, and redacted event envelopes are persisted in
`<data directory>/eeveetuber.db`. Recent messages from the current connected session are included
in later turns under the configured count, character, per-message, and local-read deadline bounds.
New sessions do not yet receive automatic cross-session memory; long-term memory extraction
remains a later milestone.

## Development

Prerequisites: Python 3.12 and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run mypy
uv run eeveetuber
```

The server defaults to `127.0.0.1:12393`. Runtime data defaults to the platform-specific
application data directory and can be overridden with `EEVEETUBER_DATA_DIR`.

## Source provenance

New core code is clean-room code written for Eeveetuber. If a future adapter directly
adapts source from Open-LLM-VTuber or another project, the file must carry the provenance
header described in [docs/PROVENANCE.md](docs/PROVENANCE.md).
