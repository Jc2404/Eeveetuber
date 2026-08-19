import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from eeveetuber.adapters.fake import FakeModelProvider
from eeveetuber.api.app import create_app
from eeveetuber.api.audio_frames import decode_audio_frame
from eeveetuber.api.protocol import WEBSOCKET_SUBPROTOCOL_BINARY_AUDIO
from eeveetuber.config import AppSettings
from eeveetuber.dialogue import DialogueRequest, ModelCompleted, ModelStopReason, ModelStreamEvent
from eeveetuber.runtime import CancellationToken


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


def _receive_until_complete(websocket: object) -> list[dict[str, object]]:
    received: list[dict[str, object]] = []
    while not received or received[-1]["type"] != "utterance.completed":
        message = websocket.receive_json()  # type: ignore[attr-defined]
        received.append(message)
        assert message["type"] != "turn.failed", message
    return received


def test_health_and_incremental_websocket_turn(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_dir=tmp_path, _env_file=None))

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"
        assert health.json()["adapters"] == {"model": "fake", "speech": "fake"}

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
        records = client.app.state.resources.store.messages.list_session(
            str(failed["session_id"])
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
