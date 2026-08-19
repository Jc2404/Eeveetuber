from uuid import UUID

import pytest
from pydantic import ValidationError

from eeveetuber.api.protocol import (
    AvatarPresentationAckMessage,
    AvatarPresentationAckState,
    AvatarRendererState,
    AvatarRendererStatusMessage,
    PingMessage,
    PlaybackAckMessage,
    PlaybackState,
    StatusCode,
    StatusMessage,
    StatusScope,
    TextTurnMessage,
    VoiceCaptureStartMessage,
    VoiceCaptureStopMessage,
    parse_client_message,
)


def test_parses_discriminated_text_turn() -> None:
    parsed = parse_client_message('{"protocol_version":1,"type":"turn.text","text":"hello"}')

    assert isinstance(parsed, TextTurnMessage)
    assert parsed.text == "hello"
    assert isinstance(parsed.message_id, UUID)


def test_rejects_unknown_fields_and_message_type() -> None:
    with pytest.raises(ValidationError):
        parse_client_message('{"protocol_version":1,"type":"ping","unexpected":true}')

    with pytest.raises(ValidationError):
        parse_client_message('{"protocol_version":1,"type":"unknown"}')


def test_ping_is_versioned() -> None:
    parsed = parse_client_message('{"protocol_version":1,"type":"ping"}')

    assert isinstance(parsed, PingMessage)
    assert parsed.protocol_version == 1


def test_voice_capture_lifecycle_messages_are_typed() -> None:
    stream_id = "00000000-0000-0000-0000-000000000070"
    started = parse_client_message(
        '{"protocol_version":1,"type":"voice.capture.start",'
        f'"stream_id":"{stream_id}","sample_rate_hz":16000,"channels":1}}'
    )
    stopped = parse_client_message(
        '{"protocol_version":1,"type":"voice.capture.stop",'
        f'"stream_id":"{stream_id}","reason":"operator_requested"}}'
    )

    assert isinstance(started, VoiceCaptureStartMessage)
    assert started.encoding == "pcm_s16le"
    assert isinstance(stopped, VoiceCaptureStopMessage)
    assert stopped.stream_id == started.stream_id


@pytest.mark.parametrize(
    "raw",
    [
        '{"protocol_version":1,"type":"voice.capture.start",'
        '"stream_id":"00000000-0000-0000-0000-000000000070",'
        '"sample_rate_hz":7999,"channels":1}',
        '{"protocol_version":1,"type":"voice.capture.start",'
        '"stream_id":"00000000-0000-0000-0000-000000000070",'
        '"sample_rate_hz":16000,"channels":0}',
        '{"protocol_version":1,"type":"voice.capture.stop",'
        '"stream_id":"00000000-0000-0000-0000-000000000070","reason":""}',
    ],
)
def test_invalid_voice_capture_lifecycle_is_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        parse_client_message(raw)


def test_playback_ack_is_typed_and_correlated() -> None:
    parsed = parse_client_message(
        """{
          "protocol_version": 1,
          "type": "playback.ack",
          "session_id": "00000000-0000-0000-0000-000000000001",
          "audio_event_id": "00000000-0000-0000-0000-000000000002",
          "generation": 3,
          "event_sequence": 12,
          "segment_id": "00000000-0000-0000-0000-000000000003",
          "chunk_index": 1,
          "state": "completed",
          "client_monotonic_ms": 12345,
          "played_ms": 810
        }"""
    )

    assert isinstance(parsed, PlaybackAckMessage)
    assert parsed.state is PlaybackState.COMPLETED
    assert parsed.event_sequence == 12
    assert parsed.played_ms == 810


@pytest.mark.parametrize(
    "fragment",
    [
        '"generation":-1',
        '"state":"invented"',
        '"client_monotonic_ms":-1',
        '"detail":"' + ("x" * 501) + '"',
    ],
)
def test_invalid_playback_ack_is_rejected(fragment: str) -> None:
    fields = {
        "generation": '"generation":0',
        "state": '"state":"queued"',
        "client_monotonic_ms": '"client_monotonic_ms":0',
        "detail": '"detail":null',
    }
    for name in fields:
        if fragment.startswith(f'"{name}"'):
            fields[name] = fragment
    raw = (
        '{"protocol_version":1,"type":"playback.ack",'
        '"session_id":"00000000-0000-0000-0000-000000000001",'
        '"audio_event_id":"00000000-0000-0000-0000-000000000002",'
        '"event_sequence":1,'
        '"segment_id":"00000000-0000-0000-0000-000000000003",'
        '"chunk_index":0,'
        + ",".join(fields.values())
        + "}"
    )
    with pytest.raises(ValidationError):
        parse_client_message(raw)


def test_status_message_has_stable_typed_fields() -> None:
    status = StatusMessage(
        session_id=UUID("00000000-0000-0000-0000-000000000001"),
        correlation_id=UUID("00000000-0000-0000-0000-000000000002"),
        sequence=9,
        generation=2,
        scope=StatusScope.AUDIO,
        status=StatusCode.DEGRADED,
        detail="primary output unavailable",
        recoverable=True,
    )

    primitive = status.model_dump(mode="json")
    assert primitive["protocol_version"] == 1
    assert primitive["type"] == "session.status"
    assert primitive["scope"] == "audio"
    assert primitive["status"] == "degraded"
    assert primitive["recoverable"] is True


def test_avatar_renderer_status_and_presentation_ack_are_typed() -> None:
    status = parse_client_message(
        '{"protocol_version":1,"type":"avatar.renderer.status",'
        '"avatar_id":"eevee_v1","revision":"profile-v2","state":"ready",'
        '"client_monotonic_ms":120}'
    )
    ack = parse_client_message(
        '{"protocol_version":1,"type":"avatar.presentation.ack",'
        '"avatar_id":"eevee_v1","revision":"profile-v2",'
        '"command_id":"00000000-0000-0000-0000-000000000099",'
        '"generation":4,"scheduler_sequence":18,"state":"applied",'
        '"client_monotonic_ms":125}'
    )

    assert isinstance(status, AvatarRendererStatusMessage)
    assert status.state is AvatarRendererState.READY
    assert isinstance(ack, AvatarPresentationAckMessage)
    assert ack.state is AvatarPresentationAckState.APPLIED
    assert ack.scheduler_sequence == 18


@pytest.mark.parametrize(
    "raw",
    [
        '{"protocol_version":1,"type":"avatar.renderer.status",'
        '"avatar_id":"bad id","revision":"v1","state":"ready",'
        '"client_monotonic_ms":1}',
        '{"protocol_version":1,"type":"avatar.renderer.status",'
        '"avatar_id":"eevee","revision":"v1","state":"unknown",'
        '"client_monotonic_ms":1}',
        '{"protocol_version":1,"type":"avatar.presentation.ack",'
        '"avatar_id":"eevee","revision":"v1",'
        '"command_id":"00000000-0000-0000-0000-000000000099",'
        '"generation":-1,"scheduler_sequence":0,"state":"applied",'
        '"client_monotonic_ms":1}',
    ],
)
def test_invalid_avatar_client_messages_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        parse_client_message(raw)
