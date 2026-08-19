from __future__ import annotations

import struct
from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from eeveetuber.media import (
    AsrFinal,
    AsrPartial,
    PcmFormat,
    PcmFrame,
    PcmUtterance,
    UtteranceEndReason,
)

STREAM_ID = UUID("00000000-0000-0000-0000-000000000010")
UTTERANCE_ID = UUID("00000000-0000-0000-0000-000000000011")
FORMAT = PcmFormat(sample_rate_hz=16_000)


def _frame(sequence: int, *, amplitude: int = 100) -> PcmFrame:
    return PcmFrame(
        stream_id=STREAM_ID,
        sequence=sequence,
        captured_at_monotonic_ns=sequence * 10_000_000,
        format=FORMAT,
        pcm=struct.pack("<160h", *([amplitude] * 160)),
    )


def test_pcm_frame_is_immutable_timestamped_and_has_exact_duration() -> None:
    frame = _frame(3)

    assert frame.sample_frame_count == 160
    assert frame.duration_ns == 10_000_000
    assert frame.end_monotonic_ns == 40_000_000
    with pytest.raises(FrozenInstanceError):
        frame.sequence = 9  # type: ignore[misc]


def test_pcm_frame_rejects_mutable_or_misaligned_audio() -> None:
    with pytest.raises(TypeError, match="immutable bytes"):
        PcmFrame(STREAM_ID, 0, 0, FORMAT, bytearray(b"\x00\x00"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="complete sample"):
        PcmFrame(STREAM_ID, 0, 0, FORMAT, b"\x00")


def test_bounded_utterance_validates_identity_order_and_exposes_transient_pcm() -> None:
    frames = (_frame(0), _frame(1))
    utterance = PcmUtterance(
        utterance_id=UTTERANCE_ID,
        stream_id=STREAM_ID,
        format=FORMAT,
        frames=frames,
        speech_started_at_monotonic_ns=0,
        speech_ended_at_monotonic_ns=20_000_000,
        end_reason=UtteranceEndReason.STREAM_ENDED,
    )

    assert utterance.byte_count == 640
    assert utterance.audio_duration_ns == 20_000_000
    assert utterance.pcm == frames[0].pcm + frames[1].pcm

    with pytest.raises(ValueError, match="strictly increasing"):
        PcmUtterance(
            utterance_id=UTTERANCE_ID,
            stream_id=STREAM_ID,
            format=FORMAT,
            frames=(frames[1], frames[0]),
            speech_started_at_monotonic_ns=0,
            speech_ended_at_monotonic_ns=20_000_000,
            end_reason=UtteranceEndReason.STREAM_ENDED,
        )


def test_asr_events_allow_empty_final_but_validate_metadata() -> None:
    partial = AsrPartial(UTTERANCE_ID, revision=0, text="hel", confidence=0.75)
    final = AsrFinal(UTTERANCE_ID, text="")

    assert partial.text == "hel"
    assert final.text == ""
    with pytest.raises(ValueError, match="confidence"):
        AsrFinal(UTTERANCE_ID, text="bad", confidence=1.1)
    with pytest.raises(ValueError, match="revision"):
        AsrPartial(UTTERANCE_ID, revision=-1, text="bad")
