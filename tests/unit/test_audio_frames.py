from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from eeveetuber.api.audio_frames import (
    AUDIO_FRAME_FIXED_HEADER_BYTES,
    AUDIO_FRAME_MAGIC,
    AUDIO_FRAME_VERSION,
    AudioFrame,
    AudioFrameError,
    AudioFrameFlags,
    decode_audio_frame,
    encode_audio_frame,
)


def _frame(**changes: object) -> AudioFrame:
    frame = AudioFrame(
        session_id=UUID("00000000-0000-0000-0000-000000000001"),
        event_id=UUID("00000000-0000-0000-0000-000000000002"),
        correlation_id=UUID("00000000-0000-0000-0000-000000000003"),
        turn_id=UUID("00000000-0000-0000-0000-000000000004"),
        segment_id=UUID("00000000-0000-0000-0000-000000000005"),
        generation=7,
        event_sequence=10_000_000_000,
        segment_sequence=3,
        chunk_index=4,
        sample_rate_hz=24_000,
        channels=1,
        media_type="audio/ogg;codecs=opus",
        audio=b"\x01\x02voice",
        duration_ms=125,
        flags=AudioFrameFlags.FINAL_CHUNK | AudioFrameFlags.END_UTTERANCE,
    )
    return replace(frame, **changes)


def test_binary_audio_frame_is_deterministic_and_round_trips() -> None:
    frame = _frame()

    first = encode_audio_frame(frame)
    second = encode_audio_frame(frame)

    assert first == second
    assert first[:4] == AUDIO_FRAME_MAGIC
    assert first[4] == AUDIO_FRAME_VERSION
    assert len(first) == AUDIO_FRAME_FIXED_HEADER_BYTES + len(frame.media_type) + len(frame.audio)
    assert decode_audio_frame(first) == frame
    assert encode_audio_frame(decode_audio_frame(first)) == first


def test_unknown_duration_and_memoryview_input_round_trip() -> None:
    frame = _frame(duration_ms=None, flags=AudioFrameFlags.NONE, audio=b"")
    packet = encode_audio_frame(frame)

    restored = decode_audio_frame(memoryview(packet))

    assert restored.duration_ms is None
    assert restored.audio == b""
    assert restored.flags is AudioFrameFlags.NONE


@pytest.mark.parametrize(
    ("offset", "value", "message"),
    [
        (0, ord("X"), "magic"),
        (4, 99, "version"),
        (5, 0x80, "flag"),
    ],
)
def test_invalid_magic_version_and_flags_are_rejected(
    offset: int, value: int, message: str
) -> None:
    packet = bytearray(encode_audio_frame(_frame()))
    packet[offset] = value

    with pytest.raises(AudioFrameError, match=message):
        decode_audio_frame(packet)


def test_truncated_extended_and_inconsistent_packets_are_rejected() -> None:
    packet = encode_audio_frame(_frame())
    with pytest.raises(AudioFrameError, match="shorter"):
        decode_audio_frame(packet[:20])
    with pytest.raises(AudioFrameError, match="length mismatch"):
        decode_audio_frame(packet[:-1])
    with pytest.raises(AudioFrameError, match="length mismatch"):
        decode_audio_frame(packet + b"trailing")

    inconsistent_header = bytearray(packet)
    inconsistent_header[6:8] = AUDIO_FRAME_FIXED_HEADER_BYTES.to_bytes(2, "big")
    with pytest.raises(AudioFrameError, match="header length"):
        decode_audio_frame(inconsistent_header)


def test_decoder_enforces_caller_payload_limit_before_copying() -> None:
    packet = encode_audio_frame(_frame(audio=b"12345"))
    with pytest.raises(AudioFrameError, match="configured limit"):
        decode_audio_frame(packet, max_payload_bytes=4)


@pytest.mark.parametrize(
    ("changes", "exception", "message"),
    [
        ({"generation": -1}, ValueError, "generation"),
        ({"event_sequence": 2**64}, ValueError, "event_sequence"),
        ({"sample_rate_hz": 0}, ValueError, "sample_rate"),
        ({"channels": 0}, ValueError, "channels"),
        ({"duration_ms": 0xFFFFFFFF}, ValueError, "duration"),
        ({"media_type": "application/octet-stream"}, ValueError, "media_type"),
        ({"media_type": "audio/bad value"}, ValueError, "media_type"),
        ({"audio": bytearray(b"bad")}, TypeError, "audio"),
    ],
)
def test_frame_metadata_bounds_are_validated(
    changes: dict[str, object], exception: type[Exception], message: str
) -> None:
    with pytest.raises(exception, match=message):
        _frame(**changes)
