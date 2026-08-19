# Eeveetuber

Eeveetuber is a low-latency, memory-first VTuber agent runtime. It combines a responsive
conversation/media plane with a separate durable work and memory plane.

The implementation is being built from the architecture and requirements in
[PROJECT_ARCHITECTURE_AND_REQUIREMENTS.md](PROJECT_ARCHITECTURE_AND_REQUIREMENTS.md).
Current completion and verification results are tracked in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## Current milestone

The initial backbone provides:

- versioned typed events and per-session isolation;
- bounded actor mailboxes and cancellation generations;
- a cached, revision-pinned context snapshot;
- incremental utterance segments suitable for early TTS;
- semantic avatar cues and deterministic arbitration;
- SQLite-backed transcript/event persistence;
- a versioned FastAPI/WebSocket vertical tracer with deterministic fake adapters.

It intentionally does not yet include production ASR, TTS, LLM, Live2D, or public-chat
adapters. Those will be added behind contracts after the backbone passes isolation,
cancellation, latency, and replay tests.

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
