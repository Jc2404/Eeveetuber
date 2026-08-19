from pathlib import Path

from fastapi.testclient import TestClient

from eeveetuber.adapters.fake import FakeModelProvider
from eeveetuber.api.app import create_app
from eeveetuber.config import AppSettings


def _receive_until_complete(websocket: object) -> list[dict[str, object]]:
    received: list[dict[str, object]] = []
    while not received or received[-1]["type"] != "utterance.completed":
        message = websocket.receive_json()  # type: ignore[attr-defined]
        received.append(message)
        assert message["type"] != "turn.failed", message
    return received


def test_health_and_incremental_websocket_turn(tmp_path: Path) -> None:
    app = create_app(AppSettings(data_dir=tmp_path))

    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"

        with client.websocket_connect("/v1/ws") as websocket:
            ready = websocket.receive_json()
            assert ready["type"] == "session.ready"

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


def test_replacement_turn_rejects_late_first_turn_output(tmp_path: Path) -> None:
    app = create_app(
        AppSettings(data_dir=tmp_path),
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
    app = create_app(AppSettings(data_dir=tmp_path))

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
