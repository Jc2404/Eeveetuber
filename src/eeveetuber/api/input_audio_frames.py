"""Versioned binary WebSocket frames for live microphone PCM input.

EVIF v1 is deliberately small and fixed-width so the realtime ingress path can
validate a complete WebSocket message before constructing domain audio values.
The header is encoded in network byte order; the PCM payload remains signed
16-bit little-endian as declared by its encoding field.
"""

from __future__ import annotations

from dataclasses import dataclass
from struct import Struct
from uuid import UUID

from eeveetuber.media.types import PcmEncoding, PcmFormat, PcmFrame

VOICE_INPUT_FRAME_MAGIC = b"EVIF"
VOICE_INPUT_FRAME_VERSION = 1
VOICE_INPUT_FRAME_FIXED_HEADER_BYTES = 52
VOICE_INPUT_ENCODING_PCM_S16LE = 1

_MAX_U16 = 0xFFFF
_MAX_U32 = 0xFFFFFFFF
_MAX_U64 = 0xFFFFFFFFFFFFFFFF

# magic, version, flags, header bytes, stream UUID, sequence, capture time,
# sample rate, channels, encoding, reserved byte, payload length.
_HEADER = Struct("!4sBBH16sQQIHBBI")
assert _HEADER.size == VOICE_INPUT_FRAME_FIXED_HEADER_BYTES


class VoiceInputFrameError(ValueError):
    """An EVIF packet is malformed, unsupported, or outside declared bounds."""


@dataclass(frozen=True, slots=True)
class VoiceInputFrame:
    """One validated, immutable EVIF microphone frame."""

    stream_id: UUID
    sequence: int
    captured_at_monotonic_ns: int
    format: PcmFormat
    pcm: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.stream_id, UUID):
            raise TypeError("stream_id must be a UUID")
        _validate_uint("sequence", self.sequence, _MAX_U64)
        _validate_uint(
            "captured_at_monotonic_ns",
            self.captured_at_monotonic_ns,
            _MAX_U64,
        )
        if not isinstance(self.format, PcmFormat):
            raise TypeError("format must be PcmFormat")
        if self.format.encoding is not PcmEncoding.S16_LE:
            raise ValueError("voice input encoding is unsupported")
        if not isinstance(self.pcm, bytes):
            raise TypeError("pcm must be immutable bytes")
        if not self.pcm:
            raise ValueError("voice input PCM cannot be empty")
        if len(self.pcm) > _MAX_U32:
            raise ValueError("voice input PCM exceeds the format limit")
        if len(self.pcm) % self.format.bytes_per_sample_frame:
            raise ValueError("voice input PCM must contain complete sample frames")

    def to_pcm_frame(self) -> PcmFrame:
        """Convert the transport value into the framework-neutral media value."""

        return PcmFrame(
            stream_id=self.stream_id,
            sequence=self.sequence,
            captured_at_monotonic_ns=self.captured_at_monotonic_ns,
            format=self.format,
            pcm=self.pcm,
        )


def encode_voice_input_frame(frame: VoiceInputFrame) -> bytes:
    """Serialize one validated microphone frame deterministically."""

    if not isinstance(frame, VoiceInputFrame):
        raise TypeError("frame must be VoiceInputFrame")
    header = _HEADER.pack(
        VOICE_INPUT_FRAME_MAGIC,
        VOICE_INPUT_FRAME_VERSION,
        0,
        VOICE_INPUT_FRAME_FIXED_HEADER_BYTES,
        frame.stream_id.bytes,
        frame.sequence,
        frame.captured_at_monotonic_ns,
        frame.format.sample_rate_hz,
        frame.format.channels,
        VOICE_INPUT_ENCODING_PCM_S16LE,
        0,
        len(frame.pcm),
    )
    return header + frame.pcm


def decode_voice_input_frame(
    raw: bytes | bytearray | memoryview,
    *,
    max_payload_bytes: int,
) -> VoiceInputFrame:
    """Validate and decode exactly one EVIF v1 binary WebSocket message."""

    _validate_uint("max_payload_bytes", max_payload_bytes, _MAX_U32)
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError("raw must be bytes-like")
    try:
        view = memoryview(raw).cast("B")
    except TypeError as error:
        raise VoiceInputFrameError("voice input frame must be contiguous bytes") from error

    if len(view) < VOICE_INPUT_FRAME_FIXED_HEADER_BYTES:
        raise VoiceInputFrameError("voice input frame is shorter than its fixed header")

    (
        magic,
        version,
        flags,
        header_bytes,
        stream_id,
        sequence,
        captured_at_monotonic_ns,
        sample_rate_hz,
        channels,
        encoding,
        reserved,
        payload_bytes,
    ) = _HEADER.unpack_from(view)

    if magic != VOICE_INPUT_FRAME_MAGIC:
        raise VoiceInputFrameError("invalid voice input frame magic")
    if version != VOICE_INPUT_FRAME_VERSION:
        raise VoiceInputFrameError("unsupported voice input frame version")
    if flags != 0:
        raise VoiceInputFrameError("voice input frame flags must be zero")
    if header_bytes != VOICE_INPUT_FRAME_FIXED_HEADER_BYTES:
        raise VoiceInputFrameError("voice input frame header length is invalid")
    if encoding != VOICE_INPUT_ENCODING_PCM_S16LE:
        raise VoiceInputFrameError("voice input frame encoding is unsupported")
    if reserved != 0:
        raise VoiceInputFrameError("voice input frame reserved byte must be zero")
    if payload_bytes == 0:
        raise VoiceInputFrameError("voice input frame PCM cannot be empty")
    if payload_bytes > max_payload_bytes:
        raise VoiceInputFrameError("voice input frame payload exceeds the configured limit")

    expected_bytes = VOICE_INPUT_FRAME_FIXED_HEADER_BYTES + payload_bytes
    if len(view) < expected_bytes:
        raise VoiceInputFrameError("voice input frame payload is truncated")
    if len(view) > expected_bytes:
        raise VoiceInputFrameError("voice input frame contains trailing bytes")

    try:
        pcm_format = PcmFormat(
            sample_rate_hz=sample_rate_hz,
            channels=channels,
            encoding=PcmEncoding.S16_LE,
        )
        return VoiceInputFrame(
            stream_id=UUID(bytes=bytes(stream_id)),
            sequence=sequence,
            captured_at_monotonic_ns=captured_at_monotonic_ns,
            format=pcm_format,
            pcm=bytes(view[VOICE_INPUT_FRAME_FIXED_HEADER_BYTES:]),
        )
    except (TypeError, ValueError) as error:
        raise VoiceInputFrameError("voice input frame metadata or PCM is invalid") from error


def _validate_uint(name: str, value: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not 0 <= value <= maximum:
        raise ValueError(f"{name} must be between 0 and {maximum}")


__all__ = [
    "VOICE_INPUT_ENCODING_PCM_S16LE",
    "VOICE_INPUT_FRAME_FIXED_HEADER_BYTES",
    "VOICE_INPUT_FRAME_MAGIC",
    "VOICE_INPUT_FRAME_VERSION",
    "VoiceInputFrame",
    "VoiceInputFrameError",
    "decode_voice_input_frame",
    "encode_voice_input_frame",
]
