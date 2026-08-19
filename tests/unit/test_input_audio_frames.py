from __future__ import annotations

from dataclasses import replace
from struct import Struct
from uuid import UUID

import pytest

from eeveetuber.api.input_audio_frames import (
    VOICE_INPUT_ENCODING_PCM_S16LE,
    VOICE_INPUT_FRAME_FIXED_HEADER_BYTES,
    VOICE_INPUT_FRAME_MAGIC,
    VOICE_INPUT_FRAME_VERSION,
    VoiceInputFrame,
    VoiceInputFrameError,
    decode_voice_input_frame,
    encode_voice_input_frame,
)
from eeveetuber.media.types import PcmEncoding, PcmFormat, PcmFrame

_HEADER = Struct("!4sBBH16sQQIHBBI")


def _frame(**changes: object) -> VoiceInputFrame:
    frame = VoiceInputFrame(
        stream_id=UUID("00000000-0000-0000-0000-000000000123"),
        sequence=0x0102030405060708,
        captured_at_monotonic_ns=0x1112131415161718,
        format=PcmFormat(
            sample_rate_hz=24_000,
            channels=2,
            encoding=PcmEncoding.S16_LE,
        ),
        pcm=b"\x01\x02\x03\x04\x05\x06\x07\x08",
    )
    return replace(frame, **changes)


def test_evif_v1_is_fixed_width_network_order_and_round_trips() -> None:
    frame = _frame()

    first = encode_voice_input_frame(frame)
    second = encode_voice_input_frame(frame)

    assert first == second
    assert len(first) == VOICE_INPUT_FRAME_FIXED_HEADER_BYTES + len(frame.pcm)
    assert first[:4] == VOICE_INPUT_FRAME_MAGIC
    assert first[4] == VOICE_INPUT_FRAME_VERSION
    assert first[5] == 0
    assert int.from_bytes(first[6:8], "big") == VOICE_INPUT_FRAME_FIXED_HEADER_BYTES
    assert first[8:24] == frame.stream_id.bytes
    assert int.from_bytes(first[24:32], "big") == frame.sequence
    assert int.from_bytes(first[32:40], "big") == frame.captured_at_monotonic_ns
    assert int.from_bytes(first[40:44], "big") == 24_000
    assert int.from_bytes(first[44:46], "big") == 2
    assert first[46] == VOICE_INPUT_ENCODING_PCM_S16LE
    assert first[47] == 0
    assert int.from_bytes(first[48:52], "big") == len(frame.pcm)
    assert decode_voice_input_frame(first, max_payload_bytes=1024) == frame
    assert encode_voice_input_frame(
        decode_voice_input_frame(memoryview(first), max_payload_bytes=1024)
    ) == first


def test_transport_frame_converts_to_domain_pcm_frame_without_metadata_loss() -> None:
    frame = _frame()

    pcm_frame = frame.to_pcm_frame()

    assert pcm_frame == PcmFrame(
        stream_id=frame.stream_id,
        sequence=frame.sequence,
        captured_at_monotonic_ns=frame.captured_at_monotonic_ns,
        format=frame.format,
        pcm=frame.pcm,
    )


@pytest.mark.parametrize(
    ("changes", "exception", "message"),
    [
        ({"stream_id": "not-a-uuid"}, TypeError, "stream_id"),
        ({"sequence": -1}, ValueError, "sequence"),
        ({"sequence": 2**64}, ValueError, "sequence"),
        ({"captured_at_monotonic_ns": -1}, ValueError, "captured_at"),
        ({"captured_at_monotonic_ns": 2**64}, ValueError, "captured_at"),
        ({"format": "pcm"}, TypeError, "format"),
        ({"pcm": bytearray(b"\x00\x00")}, TypeError, "immutable bytes"),
        ({"pcm": b""}, ValueError, "cannot be empty"),
        ({"pcm": b"\x00\x00"}, ValueError, "complete sample frames"),
    ],
)
def test_voice_input_value_enforces_serializable_bounds(
    changes: dict[str, object], exception: type[Exception], message: str
) -> None:
    with pytest.raises(exception, match=message):
        _frame(**changes)


@pytest.mark.parametrize(
    ("offset", "value", "message"),
    [
        (0, ord("X"), "magic"),
        (4, 2, "version"),
        (5, 1, "flags"),
        (7, 51, "header length"),
        (46, 2, "encoding"),
        (47, 1, "reserved"),
    ],
)
def test_decoder_rejects_unsupported_header_fields(
    offset: int, value: int, message: str
) -> None:
    packet = bytearray(encode_voice_input_frame(_frame()))
    packet[offset] = value

    with pytest.raises(VoiceInputFrameError, match=message):
        decode_voice_input_frame(packet, max_payload_bytes=1024)


def test_decoder_distinguishes_short_header_truncated_payload_and_trailing_bytes() -> None:
    packet = encode_voice_input_frame(_frame())

    with pytest.raises(VoiceInputFrameError, match="shorter"):
        decode_voice_input_frame(packet[:51], max_payload_bytes=1024)
    with pytest.raises(VoiceInputFrameError, match="truncated"):
        decode_voice_input_frame(packet[:-1], max_payload_bytes=1024)
    with pytest.raises(VoiceInputFrameError, match="trailing"):
        decode_voice_input_frame(packet + b"x", max_payload_bytes=1024)


def test_decoder_rejects_empty_oversize_and_incomplete_pcm_before_returning_data() -> None:
    frame = _frame()
    packet = encode_voice_input_frame(frame)

    with pytest.raises(VoiceInputFrameError, match="configured limit"):
        decode_voice_input_frame(packet, max_payload_bytes=len(frame.pcm) - 1)

    empty_packet = bytearray(packet[:VOICE_INPUT_FRAME_FIXED_HEADER_BYTES])
    empty_packet[48:52] = (0).to_bytes(4, "big")
    with pytest.raises(VoiceInputFrameError, match="cannot be empty"):
        decode_voice_input_frame(empty_packet, max_payload_bytes=1024)

    incomplete_packet = bytearray(packet[:-1])
    incomplete_packet[48:52] = (len(frame.pcm) - 1).to_bytes(4, "big")
    with pytest.raises(VoiceInputFrameError, match="metadata or PCM"):
        decode_voice_input_frame(incomplete_packet, max_payload_bytes=1024)


@pytest.mark.parametrize(
    ("sample_rate_hz", "channels"),
    [(7_999, 1), (192_001, 1), (24_000, 0), (24_000, 9)],
)
def test_decoder_rejects_pcm_formats_outside_domain_bounds(
    sample_rate_hz: int, channels: int
) -> None:
    packet = bytearray(encode_voice_input_frame(_frame()))
    packet[40:44] = sample_rate_hz.to_bytes(4, "big")
    packet[44:46] = channels.to_bytes(2, "big")

    with pytest.raises(VoiceInputFrameError, match="metadata or PCM"):
        decode_voice_input_frame(packet, max_payload_bytes=1024)


@pytest.mark.parametrize("limit", [-1, 2**32, True, 1.5])
def test_decoder_validates_the_payload_limit(limit: object) -> None:
    with pytest.raises((TypeError, ValueError), match="max_payload_bytes"):
        decode_voice_input_frame(encode_voice_input_frame(_frame()), max_payload_bytes=limit)  # type: ignore[arg-type]


def test_decoder_rejects_non_bytes_and_noncontiguous_buffers_without_echoing_data() -> None:
    with pytest.raises(TypeError, match="bytes-like"):
        decode_voice_input_frame("super-secret-audio", max_payload_bytes=1024)  # type: ignore[arg-type]

    packet = encode_voice_input_frame(_frame())
    noncontiguous = memoryview(packet)[::2]
    with pytest.raises(VoiceInputFrameError, match="contiguous bytes") as raised:
        decode_voice_input_frame(noncontiguous, max_payload_bytes=1024)
    assert "secret" not in str(raised.value)


def test_decoder_errors_do_not_embed_packet_content_or_unsupported_values() -> None:
    fields = list(_HEADER.unpack(encode_voice_input_frame(_frame())[:52]))
    fields[1] = 99
    packet = _HEADER.pack(*fields) + b"private microphone content"

    with pytest.raises(VoiceInputFrameError) as raised:
        decode_voice_input_frame(packet, max_payload_bytes=1024)

    message = str(raised.value)
    assert "99" not in message
    assert "private microphone content" not in message


def test_encoder_requires_the_transport_value() -> None:
    with pytest.raises(TypeError, match="VoiceInputFrame"):
        encode_voice_input_frame(object())  # type: ignore[arg-type]
