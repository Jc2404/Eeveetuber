from __future__ import annotations

import struct
from uuid import UUID

import pytest

from eeveetuber.media import (
    EnergyVadConfig,
    EnergyVoiceActivityDetector,
    PcmFormat,
    PcmFrame,
    UtteranceEndReason,
    VadSpeechEnded,
    VadSpeechStarted,
)

STREAM_ID = UUID("00000000-0000-0000-0000-000000000020")
OTHER_STREAM_ID = UUID("00000000-0000-0000-0000-000000000021")
FORMAT = PcmFormat(sample_rate_hz=16_000)


def _frame(sequence: int, amplitude: int, *, stream_id: UUID = STREAM_ID) -> PcmFrame:
    return PcmFrame(
        stream_id=stream_id,
        sequence=sequence,
        captured_at_monotonic_ns=sequence * 10_000_000,
        format=FORMAT,
        pcm=struct.pack("<160h", *([amplitude] * 160)),
    )


def _vad(**changes: int) -> EnergyVoiceActivityDetector:
    values = {
        "speech_start_threshold": 1_000,
        "speech_end_threshold": 500,
        "speech_start_frames": 2,
        "speech_end_frames": 2,
        "pre_roll_frames": 2,
        "max_utterance_duration_ms": 1_000,
        "max_utterance_bytes": 32_000,
    }
    values.update(changes)
    return EnergyVoiceActivityDetector(EnergyVadConfig(**values))


def test_vad_applies_pre_roll_hysteresis_and_trailing_silence() -> None:
    vad = _vad()

    assert vad.process(_frame(0, 0)) == ()
    assert vad.process(_frame(1, 0)) == ()
    assert vad.process(_frame(2, 1_200)) == ()
    started_events = vad.process(_frame(3, 1_200))

    assert len(started_events) == 1
    started = started_events[0]
    assert isinstance(started, VadSpeechStarted)
    assert started.at_monotonic_ns == 20_000_000
    assert started.trigger_sequence == 3
    assert started.pre_roll_frame_count == 2
    assert vad.speech_active

    # Between the start/end thresholds remains speech while the detector is active.
    assert vad.process(_frame(4, 700)) == ()
    assert vad.process(_frame(5, 0)) == ()
    ended_events = vad.process(_frame(6, 0))

    assert len(ended_events) == 1
    ended = ended_events[0]
    assert isinstance(ended, VadSpeechEnded)
    assert ended.reason is UtteranceEndReason.SILENCE
    assert ended.at_monotonic_ns == 50_000_000
    assert [frame.sequence for frame in ended.utterance.frames] == list(range(7))
    assert ended.utterance.byte_count <= vad.config.max_utterance_bytes
    assert not vad.speech_active
    assert vad.buffered_bytes == 0


def test_vad_ids_and_decisions_are_deterministic_but_instances_are_isolated() -> None:
    first = _vad(pre_roll_frames=0)
    second = _vad(pre_roll_frames=0)

    assert first.process(_frame(0, 1_500)) == ()
    first_start = first.process(_frame(1, 1_500))[0]
    assert second.process(_frame(0, 1_500)) == ()
    second_start = second.process(_frame(1, 1_500))[0]

    assert isinstance(first_start, VadSpeechStarted)
    assert isinstance(second_start, VadSpeechStarted)
    assert first_start.utterance_id == second_start.utterance_id
    assert first.speech_active and second.speech_active

    first_end = first.finish_stream()
    assert first_end is not None
    assert first_end.reason is UtteranceEndReason.STREAM_ENDED
    assert not first.speech_active
    assert second.speech_active


@pytest.mark.parametrize(
    ("changes", "expected_reason", "frame_count"),
    [
        ({"max_utterance_duration_ms": 30}, UtteranceEndReason.MAX_DURATION, 3),
        ({"max_utterance_bytes": 640}, UtteranceEndReason.MAX_BYTES, 2),
    ],
)
def test_vad_enforces_exact_duration_and_byte_bounds(
    changes: dict[str, int], expected_reason: UtteranceEndReason, frame_count: int
) -> None:
    vad = _vad(
        speech_start_frames=1,
        speech_end_frames=2,
        pre_roll_frames=0,
        **changes,
    )

    events = []
    for sequence in range(frame_count):
        events.extend(vad.process(_frame(sequence, 1_500)))

    assert isinstance(events[0], VadSpeechStarted)
    ended = events[-1]
    assert isinstance(ended, VadSpeechEnded)
    assert ended.reason is expected_reason
    assert ended.utterance.byte_count <= vad.config.max_utterance_bytes
    assert ended.utterance.audio_duration_ns <= vad.config.max_utterance_duration_ns


def test_vad_finishing_idle_discards_pre_roll_and_allows_a_new_stream() -> None:
    vad = _vad()
    vad.process(_frame(0, 0))

    assert vad.buffered_bytes == 320
    assert vad.finish_stream() is None
    assert vad.buffered_bytes == 0
    assert vad.process(_frame(0, 0, stream_id=OTHER_STREAM_ID)) == ()


def test_vad_rejects_mixed_streams_overlapping_frames_and_oversize_input() -> None:
    vad = _vad(max_utterance_bytes=640)
    vad.process(_frame(0, 0))

    with pytest.raises(ValueError, match="identities"):
        vad.process(_frame(1, 0, stream_id=OTHER_STREAM_ID))
    with pytest.raises(ValueError, match="strictly increasing"):
        vad.process(_frame(0, 0))

    oversized = PcmFrame(
        stream_id=STREAM_ID,
        sequence=1,
        captured_at_monotonic_ns=10_000_000,
        format=FORMAT,
        pcm=struct.pack("<400h", *([0] * 400)),
    )
    with pytest.raises(ValueError, match="exceeds max_utterance_bytes"):
        vad.process(oversized)
