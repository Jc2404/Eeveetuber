from __future__ import annotations

from pathlib import Path

OPERATOR = Path(__file__).resolve().parents[2] / "apps" / "operator"


def test_operator_client_is_dependency_free_and_exposes_required_controls() -> None:
    html = (OPERATOR / "index.html").read_text(encoding="utf-8")
    script = (OPERATOR / "app.js").read_text(encoding="utf-8")

    assert "https://" not in html
    assert 'type="module" src="./app.js"' in html
    for action in ("stop_speech", "neutral_avatar", "kill_session"):
        assert f'data-action="{action}"' in html
    assert 'type: "turn.text"' in script
    assert 'type: "playback.ack"' in script
    assert "decodeAudioFrame" in script
    assert "speech.audio_chunk" in script


def test_operator_mount_instructions_and_assets_exist() -> None:
    for name in ("index.html", "app.js", "mic-worklet.js", "styles.css", "README.md"):
        assert (OPERATOR / name).is_file()
    readme = (OPERATOR / "README.md").read_text(encoding="utf-8")
    server = (OPERATOR.parents[1] / "src" / "eeveetuber" / "api" / "app.py").read_text(
        encoding="utf-8"
    )
    assert '"/operator"' in server and "StaticFiles" in server
    assert "encode_audio_frame" in readme


def test_operator_stops_old_playback_from_generation_control_events() -> None:
    script = (OPERATOR / "app.js").read_text(encoding="utf-8")

    assert "applyAudioControl(message);" in script
    assert 'message.type === "speech.cancelled"' in script
    assert "generation > state.audioGeneration" in script
    assert 'stopActiveAudio("cancelled");' in script
    assert script.index("applyAudioControl(message);") < script.index(
        'if (message.type === "session.ready")'
    )


def test_operator_microphone_is_explicit_accessible_and_uses_external_worklet() -> None:
    html = (OPERATOR / "index.html").read_text(encoding="utf-8")
    script = (OPERATOR / "app.js").read_text(encoding="utf-8")
    worklet = (OPERATOR / "mic-worklet.js").read_text(encoding="utf-8")

    assert 'id="mic-start"' in html and ">Start microphone<" in html
    assert 'id="mic-stop"' in html and ">Stop microphone<" in html
    assert 'id="mic-state"' in html and 'aria-live="polite"' in html
    assert 'id="mic-transcript"' in html
    assert "worker-src 'self'" in html
    assert "getUserMedia" in script
    assert 'audioWorklet.addModule("./mic-worklet.js")' in script
    assert "getUserMedia" not in worklet
    assert 'registerProcessor("eeveetuber-microphone"' in worklet
    assert 'event.data?.type === "stop"' in worklet
    assert 'type: "pcm.frame"' in worklet


def test_operator_voice_capture_uses_negotiated_evif_v1_framing_and_backpressure() -> None:
    script = (OPERATOR / "app.js").read_text(encoding="utf-8")

    assert 'const VOICE_INPUT_MAGIC = "EVIF"' in script
    assert "const VOICE_INPUT_VERSION = 1" in script
    assert "const VOICE_INPUT_FIXED_HEADER_BYTES = 52" in script
    assert "const VOICE_INPUT_CHANNELS = 1" in script
    assert "data?.voice_input" in script
    assert "capability.sample_rate_hz" in script
    assert "capability.frame_duration_ms" in script
    assert "capability.max_frame_bytes" in script
    assert 'type: "voice.capture.start"' in script
    assert 'type: "voice.capture.stop"' in script
    assert 'reason: "operator_requested"' in script
    assert "view.setBigUint64(24, sequence, false)" in script
    assert "view.setBigUint64(32, capturedAtMonotonicNs, false)" in script
    assert "view.setUint32(40, sampleRateHz, false)" in script
    assert "view.setUint16(44, VOICE_INPUT_CHANNELS, false)" in script
    assert "view.setUint8(46, VOICE_INPUT_ENCODING_PCM_S16LE)" in script
    assert "view.setUint32(48, pcmBytes, false)" in script
    assert "view.setInt16(VOICE_INPUT_FIXED_HEADER_BYTES + index * 2, pcm, true)" in script
    assert "state.mic.nextCapturedAtNs += state.mic.frameDurationNs" in script
    assert "socket.bufferedAmount" in script


def test_operator_renders_partial_and_final_voice_transcripts() -> None:
    script = (OPERATOR / "app.js").read_text(encoding="utf-8")

    assert 'message.type === "voice.transcript_partial"' in script
    assert 'message.type === "voice.transcript_final"' in script
    assert "ui.micTranscript.textContent" in script
    assert 'appendHistory("owner", text.trim())' in script
