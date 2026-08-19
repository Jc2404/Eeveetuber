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
- incremental utterance segments suitable for early TTS;
- semantic avatar cues and deterministic arbitration;
- SQLite-backed transcript/event persistence;
- a versioned FastAPI/WebSocket vertical tracer with deterministic fake adapters;
- a dependency-free operator console at `http://127.0.0.1:12393/`;
- negotiated EVAF v1 binary audio frames, ordered browser playback, and correlated playback
  acknowledgements;
- configurable OpenAI-compatible streaming model and speech adapters, including keyless local
  model endpoints such as Ollama's OpenAI-compatible route.

The zero-configuration default remains fake model and fake speech. Microphone/VAD/ASR, Live2D,
selected-image input, and public-chat adapters are not implemented yet.

## Run the operator tracer

```powershell
uv run eeveetuber
```

Open `http://127.0.0.1:12393/`, connect, and send a text turn. With the default configuration the
model echoes the message and speech is deliberately fake, so the browser will report that the
fake payload cannot be decoded while still exercising the complete transport and acknowledgement path.

## Configure a real model or speech endpoint

Settings use the `EEVEETUBER_` prefix and `__` for nested fields. Copy the relevant lines from
`profiles/providers/openai-compatible.env.example` or `profiles/providers/ollama.env.example`
into a local `.env` file. For example:

```dotenv
EEVEETUBER_MODEL__PROVIDER=openai_compatible
EEVEETUBER_MODEL__BASE_URL=http://127.0.0.1:11434/v1
EEVEETUBER_MODEL__MODEL=replace-with-your-local-model
```

Omit the API key for a trusted local endpoint. Keep `.env` private; it is excluded from Git.

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
