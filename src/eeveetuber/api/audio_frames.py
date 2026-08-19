"""Version-one deterministic binary WebSocket frame for synthesized audio.

The fixed header uses network byte order and is followed by the UTF-8 media type
and the raw audio bytes. WebSocket framing provides delivery integrity; this
format adds identity, ordering, generation, and media metadata without JSON or
base64 overhead on the realtime path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag
from struct import Struct
from uuid import UUID

AUDIO_FRAME_MAGIC = b"EVAF"
AUDIO_FRAME_VERSION = 1
MAX_AUDIO_PAYLOAD_BYTES = 16 * 1024 * 1024

_UNKNOWN_DURATION = 0xFFFFFFFF
_MAX_U16 = 0xFFFF
_MAX_U32 = 0xFFFFFFFF
_MAX_U64 = 0xFFFFFFFFFFFFFFFF

# magic, version, flags, header bytes, five UUIDs, generation, event sequence,
# segment sequence, chunk index, sample rate, channels, media-type length,
# duration, payload length.
_HEADER = Struct("!4sBBH16s16s16s16s16sIQIIIHHII")
AUDIO_FRAME_FIXED_HEADER_BYTES = _HEADER.size


class AudioFrameFlags(IntFlag):
    NONE = 0
    FINAL_CHUNK = 1 << 0
    END_UTTERANCE = 1 << 1
    DISCONTINUITY = 1 << 2


_KNOWN_FLAGS = (
    AudioFrameFlags.FINAL_CHUNK
    | AudioFrameFlags.END_UTTERANCE
    | AudioFrameFlags.DISCONTINUITY
)


class AudioFrameError(ValueError):
    """The binary packet is malformed, unsupported, or outside declared bounds."""


@dataclass(frozen=True, slots=True)
class AudioFrame:
    session_id: UUID
    event_id: UUID
    correlation_id: UUID
    turn_id: UUID
    segment_id: UUID
    generation: int
    event_sequence: int
    segment_sequence: int
    chunk_index: int
    sample_rate_hz: int
    channels: int
    media_type: str
    audio: bytes
    duration_ms: int | None = None
    flags: AudioFrameFlags = AudioFrameFlags.NONE

    def __post_init__(self) -> None:
        _validate_uint("generation", self.generation, _MAX_U32)
        _validate_uint("event_sequence", self.event_sequence, _MAX_U64)
        _validate_uint("segment_sequence", self.segment_sequence, _MAX_U32)
        _validate_uint("chunk_index", self.chunk_index, _MAX_U32)
        _validate_uint("sample_rate_hz", self.sample_rate_hz, _MAX_U32, minimum=1)
        _validate_uint("channels", self.channels, _MAX_U16, minimum=1)
        if self.duration_ms is not None:
            _validate_uint("duration_ms", self.duration_ms, _MAX_U32 - 1)
        if not isinstance(self.flags, AudioFrameFlags):
            raise TypeError("flags must be AudioFrameFlags")
        if int(self.flags) & ~int(_KNOWN_FLAGS):
            raise ValueError("flags contain unsupported bits")
        try:
            media_bytes = self.media_type.encode("ascii")
        except UnicodeEncodeError as error:
            raise ValueError("media_type must be ASCII") from error
        if (
            not media_bytes
            or len(media_bytes) > _MAX_U16
            or not self.media_type.startswith("audio/")
            or any(character.isspace() for character in self.media_type)
        ):
            raise ValueError("media_type must be a non-blank audio/* MIME token")
        if not isinstance(self.audio, bytes):
            raise TypeError("audio must be bytes")
        if len(self.audio) > MAX_AUDIO_PAYLOAD_BYTES:
            raise ValueError("audio payload exceeds the transport limit")


def encode_audio_frame(frame: AudioFrame) -> bytes:
    """Serialize ``frame`` deterministically in network byte order."""

    media_type = frame.media_type.encode("ascii")
    header_bytes = _HEADER.size + len(media_type)
    duration = frame.duration_ms if frame.duration_ms is not None else _UNKNOWN_DURATION
    header = _HEADER.pack(
        AUDIO_FRAME_MAGIC,
        AUDIO_FRAME_VERSION,
        int(frame.flags),
        header_bytes,
        frame.session_id.bytes,
        frame.event_id.bytes,
        frame.correlation_id.bytes,
        frame.turn_id.bytes,
        frame.segment_id.bytes,
        frame.generation,
        frame.event_sequence,
        frame.segment_sequence,
        frame.chunk_index,
        frame.sample_rate_hz,
        frame.channels,
        len(media_type),
        duration,
        len(frame.audio),
    )
    return b"".join((header, media_type, frame.audio))


def decode_audio_frame(
    packet: bytes | bytearray | memoryview,
    *,
    max_payload_bytes: int = MAX_AUDIO_PAYLOAD_BYTES,
) -> AudioFrame:
    """Validate and decode exactly one binary WebSocket message."""

    if max_payload_bytes < 0:
        raise ValueError("max_payload_bytes cannot be negative")
    view = memoryview(packet)
    if len(view) < _HEADER.size:
        raise AudioFrameError("audio frame is shorter than the fixed header")
    (
        magic,
        version,
        raw_flags,
        header_bytes,
        session_id,
        event_id,
        correlation_id,
        turn_id,
        segment_id,
        generation,
        event_sequence,
        segment_sequence,
        chunk_index,
        sample_rate_hz,
        channels,
        media_type_bytes,
        raw_duration,
        payload_bytes,
    ) = _HEADER.unpack_from(view)

    if magic != AUDIO_FRAME_MAGIC:
        raise AudioFrameError("invalid audio frame magic")
    if version != AUDIO_FRAME_VERSION:
        raise AudioFrameError(f"unsupported audio frame version: {version}")
    if raw_flags & ~int(_KNOWN_FLAGS):
        raise AudioFrameError("audio frame contains unknown flag bits")
    expected_header_bytes = _HEADER.size + media_type_bytes
    if header_bytes != expected_header_bytes:
        raise AudioFrameError("audio frame header length is inconsistent")
    if payload_bytes > max_payload_bytes:
        raise AudioFrameError("audio frame payload exceeds the configured limit")
    expected_packet_bytes = header_bytes + payload_bytes
    if len(view) != expected_packet_bytes:
        raise AudioFrameError(
            f"audio frame length mismatch: expected {expected_packet_bytes}, got {len(view)}"
        )
    media_slice = view[_HEADER.size:header_bytes]
    try:
        media_type = bytes(media_slice).decode("ascii")
    except UnicodeDecodeError as error:
        raise AudioFrameError("audio frame media type is not ASCII") from error

    try:
        return AudioFrame(
            session_id=UUID(bytes=bytes(session_id)),
            event_id=UUID(bytes=bytes(event_id)),
            correlation_id=UUID(bytes=bytes(correlation_id)),
            turn_id=UUID(bytes=bytes(turn_id)),
            segment_id=UUID(bytes=bytes(segment_id)),
            generation=generation,
            event_sequence=event_sequence,
            segment_sequence=segment_sequence,
            chunk_index=chunk_index,
            sample_rate_hz=sample_rate_hz,
            channels=channels,
            media_type=media_type,
            audio=bytes(view[header_bytes:]),
            duration_ms=None if raw_duration == _UNKNOWN_DURATION else raw_duration,
            flags=AudioFrameFlags(raw_flags),
        )
    except (TypeError, ValueError) as error:
        raise AudioFrameError(f"invalid audio frame metadata: {error}") from error


def _validate_uint(name: str, value: int, maximum: int, *, minimum: int = 0) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
