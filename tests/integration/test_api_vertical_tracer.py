import json
import struct
import time
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from eeveetuber.adapters.fake import (
    FakeModelProvider,
    FakeSpeechRecognizer,
    FakeSpeechSynthesizer,
)
from eeveetuber.api.app import create_app
from eeveetuber.api.audio_frames import decode_audio_frame
from eeveetuber.api.input_audio_frames import VoiceInputFrame, encode_voice_input_frame
from eeveetuber.api.protocol import WEBSOCKET_SUBPROTOCOL_BINARY_AUDIO
from eeveetuber.config import (
    AppSettings,
    ConversationHistorySettings,
    VoiceInputSettings,
)
from eeveetuber.dialogue import DialogueRequest, ModelCompleted, ModelStopReason, ModelStreamEvent
from eeveetuber.domain.events import RetentionClass
from eeveetuber.media import PcmFormat, SpeechRecognizer
from eeveetuber.runtime import CancellationToken
from eeveetuber.storage import MessageRecord, SqliteStore


class EmptyLengthModel:
    async def stream(
        self,
        _request: DialogueRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        yield ModelCompleted(
            stop_reason=ModelStopReason.LENGTH,
            input_tokens=64,
            output_tokens=512,
        )


class CloseTrackingModel(FakeModelProvider):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


class CloseTrackingSpeech(FakeSpeechSynthesizer):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


def _receive_until_complete(websocket: object) -> list[dict[str, object]]:
    received: list[dict[str, object]] = []
    while not received or received[-1]["type"] != "utterance.completed":
        message = websocket.receive_json()  # type: ignore[attr-defined]
        received.append(message)
        assert message["type"] != "turn.failed", message
    return received


def _wait_for_persisted_messages(
    store: SqliteStore,
    session_id: str,
    *,
    timeout: float = 2.0,
) -> tuple[MessageRecord, ...]:
    """Observe a deliberately off-path SQLite write without assuming its latency."""

    deadline = time.monotonic() + timeout
    while True:
        records = tuple(store.messages.list_session(session_id))
        if records:
            return records
        if time.monotonic() >= deadline:
            raise AssertionError("conversation message was not persisted before the test deadline")
        time.sleep(0.01)


def test_health_and_incremental_websocket_turn(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_dir=tmp_path, _env_file=None))

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"
        assert health.json()["adapters"] == {
            "model": "fake",
            "speech": "fake",
            "asr": "fake",
        }
        assert health.json()["voice_input"] == {"enabled": True}

        root = client.get("/", follow_redirects=False)
        assert root.status_code in {302, 307}
        assert root.headers["location"] == "/operator/"
        assert client.get("/operator/").status_code == 200

        with client.websocket_connect("/v1/ws") as websocket:
            assert websocket.receive_json()["type"] == "session.ready"

            websocket.send_json(
                {"protocol_version": 1, "type": "turn.text", "text": "hello backbone"}
            )
            received = _receive_until_complete(websocket)

    event_types = [str(message["type"]) for message in received]
    assert event_types.index("context.snapshot_published") < event_types.index(
        "utterance.segment_ready"
    )
    assert event_types.index("utterance.segment_ready") < event_types.index("speech.audio_chunk")
    assert event_types[-1] == "utterance.completed"
    assert len({message["generation"] for message in received}) == 1


def test_websocket_closes_model_when_speech_factory_fails(tmp_path: Path) -> None:
    model = CloseTrackingModel()

    def fail_speech_factory() -> FakeSpeechSynthesizer:
        raise RuntimeError("speech factory failed")

    app = create_app(
        AppSettings(data_dir=tmp_path, _env_file=None),
        model_factory=lambda: model,
        speech_factory=fail_speech_factory,
    )

    with TestClient(app) as client, pytest.raises(RuntimeError, match="speech factory failed"):
        with client.websocket_connect("/v1/ws") as websocket:
            websocket.receive()

    assert model.close_calls == 1


def test_websocket_closes_session_adapters_when_asr_factory_fails(tmp_path: Path) -> None:
    model = CloseTrackingModel()
    speech = CloseTrackingSpeech()

    def fail_asr_factory() -> SpeechRecognizer:
        raise RuntimeError("ASR factory failed")

    app = create_app(
        AppSettings(data_dir=tmp_path, _env_file=None),
        model_factory=lambda: model,
        speech_factory=lambda: speech,
        asr_factory=fail_asr_factory,
    )

    with TestClient(app) as client, pytest.raises(RuntimeError, match="ASR factory failed"):
        with client.websocket_connect("/v1/ws") as websocket:
            websocket.receive()

    assert model.close_calls == 1
    assert speech.close_calls == 1


def test_binary_audio_subprotocol_and_playback_acknowledgement(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_dir=tmp_path, _env_file=None))

    with TestClient(app) as client, client.websocket_connect(
        "/v1/ws",
        subprotocols=[WEBSOCKET_SUBPROTOCOL_BINARY_AUDIO],
    ) as websocket:
        assert websocket.accepted_subprotocol == WEBSOCKET_SUBPROTOCOL_BINARY_AUDIO
        assert websocket.receive_json()["type"] == "session.ready"
        assert websocket.receive_json()["type"] == "session.status"
        websocket.send_json({"protocol_version": 1, "type": "turn.text", "text": "binary"})

        audio_frame = None
        event_types: list[str] = []
        while "playback.acknowledged" not in event_types:
            raw = websocket.receive()
            binary = raw.get("bytes")
            if isinstance(binary, bytes):
                audio_frame = decode_audio_frame(binary)
                websocket.send_json(
                    {
                        "protocol_version": 1,
                        "type": "playback.ack",
                        "session_id": str(audio_frame.session_id),
                        "audio_event_id": str(audio_frame.event_id),
                        "generation": audio_frame.generation,
                        "event_sequence": audio_frame.event_sequence,
                        "segment_id": str(audio_frame.segment_id),
                        "chunk_index": audio_frame.chunk_index,
                        "state": "completed",
                        "client_monotonic_ms": 100,
                        "played_ms": audio_frame.duration_ms,
                    }
                )
                continue
            text = raw.get("text")
            assert isinstance(text, str)
            event_types.append(str(json.loads(text)["type"]))

    assert audio_frame is not None
    assert audio_frame.audio.startswith(b"FAKE_AUDIO:")
    assert "speech.audio_chunk" not in event_types
    assert "utterance.completed" in event_types


def test_session_events_are_persisted_off_path_with_audio_redacted(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_dir=tmp_path, _env_file=None))

    with TestClient(app) as client:
        with client.websocket_connect("/v1/ws") as websocket:
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json(
                {"protocol_version": 1, "type": "turn.text", "text": "journal this"}
            )
            received = _receive_until_complete(websocket)

        accepted = next(message for message in received if message["type"] == "turn.accepted")
        audio = next(message for message in received if message["type"] == "speech.audio_chunk")
        completed = received[-1]
        store = client.app.state.resources.store
        requested_record = store.events.get(str(accepted["correlation_id"]))
        completed_record = store.events.get(str(completed["message_id"]))
        for _attempt in range(100):
            if completed_record is not None:
                break
            time.sleep(0.005)
            completed_record = store.events.get(str(completed["message_id"]))
        audio_record = store.events.get(str(audio["message_id"]))

        assert requested_record is not None
        assert requested_record.event_type == "turn.requested"
        assert completed_record is not None
        assert requested_record.payload["sequence"] < completed_record.payload["sequence"]  # type: ignore[operator]
        assert completed_record.payload["payload"]["output_tokens"] is not None  # type: ignore[index]
        assert audio_record is not None
        stored_audio = audio_record.payload["payload"]
        assert isinstance(stored_audio, dict)
        assert stored_audio["audio_redacted"] is True
        assert "audio_base64" not in stored_audio


def test_zero_visible_model_output_is_a_readable_failure_not_silent_success(
    tmp_path: Path,
) -> None:
    app = create_app(
        AppSettings(data_dir=tmp_path, _env_file=None),
        model_factory=EmptyLengthModel,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/v1/ws") as websocket:
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json(
                {"protocol_version": 1, "type": "turn.text", "text": "please answer"}
            )
            received: list[dict[str, object]] = []
            while not received or received[-1]["type"] != "turn.failed":
                received.append(websocket.receive_json())

        failed = received[-1]
        data = failed["data"]
        assert isinstance(data, dict)
        assert data["error_type"] == "ModelEmptyOutput"
        assert data["stop_reason"] == "length"
        assert data["output_tokens"] == 512
        assert "output limit" in str(data["detail"])
        assert "utterance.completed" not in {event["type"] for event in received}
        records = _wait_for_persisted_messages(
            client.app.state.resources.store,
            str(failed["session_id"]),
        )
        assert [record.content for record in records] == ["please answer"]


def test_replacement_turn_rejects_late_first_turn_output(tmp_path: Path) -> None:
    app = create_app(
        AppSettings(data_dir=tmp_path, _env_file=None),
        model_factory=lambda: FakeModelProvider(
            lambda request: f"Reply to {request.user_text}.",
            chunk_chars=2,
            delay_seconds=0.01,
        ),
    )

    with TestClient(app) as client, client.websocket_connect("/v1/ws") as websocket:
        assert websocket.receive_json()["type"] == "session.ready"
        websocket.send_json({"protocol_version": 1, "type": "turn.text", "text": "first"})
        websocket.send_json({"protocol_version": 1, "type": "turn.text", "text": "second"})
        received = _receive_until_complete(websocket)

    completed = received[-1]
    assert completed["generation"] == 2
    assert "second" in completed["data"]["display_text"]  # type: ignore[index]
    assert not [
        event
        for event in received
        if event["generation"] == 1
        and event["type"] in {"utterance.segment_ready", "speech.audio_chunk"}
    ]


def test_two_websocket_sessions_do_not_cross_talk(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_dir=tmp_path, _env_file=None))

    with (
        TestClient(app) as client,
        client.websocket_connect("/v1/ws") as first,
        client.websocket_connect("/v1/ws") as second,
    ):
        first_ready = first.receive_json()
        second_ready = second.receive_json()
        assert first_ready["session_id"] != second_ready["session_id"]
        first.send_json({"protocol_version": 1, "type": "turn.text", "text": "SENTINEL_A"})
        second.send_json({"protocol_version": 1, "type": "turn.text", "text": "SENTINEL_B"})
        first_events = _receive_until_complete(first)
        second_events = _receive_until_complete(second)

    first_text = first_events[-1]["data"]["display_text"]  # type: ignore[index]
    second_text = second_events[-1]["data"]["display_text"]  # type: ignore[index]
    assert "SENTINEL_A" in first_text and "SENTINEL_B" not in first_text
    assert "SENTINEL_B" in second_text and "SENTINEL_A" not in second_text


def test_api_wires_configured_recent_history_bounds_into_each_session(tmp_path: Path) -> None:
    model = FakeModelProvider("FIRST_ASSISTANT_SENTINEL.")
    app = create_app(
        AppSettings(
            data_dir=tmp_path,
            history=ConversationHistorySettings(max_messages=1),
            _env_file=None,
        ),
        model_factory=lambda: model,
    )

    with TestClient(app) as client, client.websocket_connect("/v1/ws") as websocket:
        assert websocket.receive_json()["type"] == "session.ready"
        websocket.send_json(
            {"protocol_version": 1, "type": "turn.text", "text": "FIRST_USER_SENTINEL"}
        )
        _receive_until_complete(websocket)
        websocket.send_json(
            {"protocol_version": 1, "type": "turn.text", "text": "SECOND_USER_SENTINEL"}
        )
        _receive_until_complete(websocket)

    second_request = model.requests[1]
    assert second_request.metadata["history_message_count"] == "1"
    assert "FIRST_ASSISTANT_SENTINEL" in second_request.system_context
    assert "FIRST_USER_SENTINEL" not in second_request.system_context


def _voice_test_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        data_dir=tmp_path,
        voice=VoiceInputSettings(
            frame_duration_ms=10,
            max_frame_bytes=1_024,
            speech_start_threshold=1_000,
            speech_end_threshold=500,
            speech_start_frames=1,
            speech_end_frames=1,
            pre_roll_frames=0,
            max_utterance_duration_ms=1_000,
            max_utterance_bytes=8_192,
            asr_timeout_ms=1_000,
            max_pending_utterances=1,
        ),
        _env_file=None,
    )


def _voice_frame(stream_id: UUID, sequence: int, amplitude: int) -> bytes:
    sample_count = 160
    return encode_voice_input_frame(
        VoiceInputFrame(
            stream_id=stream_id,
            sequence=sequence,
            captured_at_monotonic_ns=sequence * 10_000_000,
            format=PcmFormat(16_000),
            pcm=struct.pack(f"<{sample_count}h", *([amplitude] * sample_count)),
        )
    )


def test_fake_microphone_turn_runs_vad_asr_and_normal_dialogue(tmp_path: Path) -> None:
    app = create_app(_voice_test_settings(tmp_path))
    stream_id = UUID("00000000-0000-0000-0000-000000000081")

    with TestClient(app) as client, client.websocket_connect("/v1/ws") as websocket:
        ready = websocket.receive_json()
        assert ready["data"]["voice_input"] == {
            "enabled": True,
            "sample_rate_hz": 16_000,
            "channels": 1,
            "encoding": "pcm_s16le",
            "frame_duration_ms": 10,
            "max_frame_bytes": 1_024,
            "barge_in_enabled": True,
        }
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "voice.capture.start",
                "stream_id": str(stream_id),
                "sample_rate_hz": 16_000,
                "channels": 1,
            }
        )
        websocket.send_bytes(_voice_frame(stream_id, 0, 2_000))
        websocket.send_bytes(_voice_frame(stream_id, 1, 0))
        received = _receive_until_complete(websocket)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "voice.capture.stop",
                "stream_id": str(stream_id),
            }
        )

    event_types = [event["type"] for event in received]
    assert "voice.capture_started" in event_types
    assert "voice.speech_started" in event_types
    assert "voice.transcript_final" in event_types
    assert event_types.index("voice.transcript_final") < event_types.index("turn.accepted")
    completed = received[-1]
    assert completed["generation"] == 2
    assert "Hello from fake ASR" in completed["data"]["display_text"]  # type: ignore[index]


def test_partial_voice_transcripts_are_streamed_but_never_journaled(tmp_path: Path) -> None:
    app = create_app(
        _voice_test_settings(tmp_path),
        asr_factory=lambda: FakeSpeechRecognizer(
            transcript="durable final transcript",
            partials=("private draft", "private draft revised"),
            delay_seconds=0.01,
        ),
    )
    stream_id = UUID("00000000-0000-0000-0000-000000000084")

    with TestClient(app) as client:
        with client.websocket_connect("/v1/ws") as websocket:
            assert websocket.receive_json()["type"] == "session.ready"
            websocket.send_json(
                {
                    "protocol_version": 1,
                    "type": "voice.capture.start",
                    "stream_id": str(stream_id),
                    "sample_rate_hz": 16_000,
                    "channels": 1,
                }
            )
            websocket.send_bytes(_voice_frame(stream_id, 0, 2_000))
            websocket.send_bytes(_voice_frame(stream_id, 1, 0))
            received = _receive_until_complete(websocket)

        partials = [event for event in received if event["type"] == "voice.transcript_partial"]
        final = next(event for event in received if event["type"] == "voice.transcript_final")
        store = client.app.state.resources.store

        deadline = time.monotonic() + 1.0
        final_output = store.events.get(str(final["message_id"]))
        while final_output is None and time.monotonic() < deadline:
            time.sleep(0.01)
            final_output = store.events.get(str(final["message_id"]))

        assert [partial["data"]["text"] for partial in partials] == [  # type: ignore[index]
            "private draft",
            "private draft revised",
        ]
        for partial in partials:
            assert store.events.get(str(partial["message_id"])) is None
            assert store.events.get(str(partial["causation_id"])) is None

        final_input = store.events.get(str(final["causation_id"]))
        assert final_output is not None
        assert final_output.event_type == "voice.transcript_final"
        assert final_output.payload["retention"] == RetentionClass.TRANSCRIPT.value
        assert final_input is not None
        assert final_input.event_type == "turn.requested"
        assert final_input.payload["retention"] == RetentionClass.TRANSCRIPT.value


def test_voice_activity_barges_in_and_rejects_old_generation_output(tmp_path: Path) -> None:
    app = create_app(
        _voice_test_settings(tmp_path),
        model_factory=lambda: FakeModelProvider(
            lambda request: f"A deliberately extended response to {request.user_text}.",
            chunk_chars=2,
            delay_seconds=0.01,
        ),
    )
    stream_id = UUID("00000000-0000-0000-0000-000000000082")

    with TestClient(app) as client, client.websocket_connect("/v1/ws") as websocket:
        assert websocket.receive_json()["type"] == "session.ready"
        websocket.send_json({"protocol_version": 1, "type": "turn.text", "text": "first"})
        before_barge: list[dict[str, object]] = []
        while not any(event["type"] == "utterance.segment_ready" for event in before_barge):
            before_barge.append(websocket.receive_json())

        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "voice.capture.start",
                "stream_id": str(stream_id),
                "sample_rate_hz": 16_000,
                "channels": 1,
            }
        )
        sent_at = time.monotonic()
        websocket.send_bytes(_voice_frame(stream_id, 0, 2_000))
        websocket.send_bytes(_voice_frame(stream_id, 1, 0))

        after_barge: list[dict[str, object]] = []
        cancelled_latency: float | None = None
        while not (
            after_barge
            and after_barge[-1]["type"] == "utterance.completed"
            and after_barge[-1]["generation"] == 3
        ):
            event = websocket.receive_json()
            after_barge.append(event)
            if event["type"] == "speech.cancelled" and cancelled_latency is None:
                cancelled_latency = time.monotonic() - sent_at

    assert cancelled_latency is not None and cancelled_latency < 1.0
    cancellation_index = next(
        index for index, event in enumerate(after_barge) if event["type"] == "speech.cancelled"
    )
    assert not [
        event
        for event in after_barge[cancellation_index + 1 :]
        if event["generation"] == 1
        and event["type"] in {"utterance.segment_ready", "speech.audio_chunk"}
    ]
    assert "Hello from fake ASR" in after_barge[-1]["data"]["display_text"]  # type: ignore[index]


def test_invalid_voice_frame_order_closes_with_deterministic_policy_code(tmp_path: Path) -> None:
    app = create_app(_voice_test_settings(tmp_path))
    stream_id = UUID("00000000-0000-0000-0000-000000000083")

    with TestClient(app) as client, client.websocket_connect("/v1/ws") as websocket:
        assert websocket.receive_json()["type"] == "session.ready"
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "voice.capture.start",
                "stream_id": str(stream_id),
                "sample_rate_hz": 16_000,
                "channels": 1,
            }
        )
        websocket.send_bytes(_voice_frame(stream_id, 0, 0))
        websocket.send_bytes(_voice_frame(stream_id, 0, 0))
        while True:
            packet = websocket.receive()
            if packet["type"] == "websocket.close":
                break

    assert packet["code"] == 1008
    assert "order or timing" in packet["reason"]
