from __future__ import annotations

import pytest
from pydantic import ValidationError

from eeveetuber.config import AppSettings, AsrAdapterSettings, VoiceInputSettings


def test_voice_settings_preserve_realtime_defaults() -> None:
    settings = AppSettings(_env_file=None)

    assert settings.voice == VoiceInputSettings(
        enabled=True,
        sample_rate_hz=16_000,
        channels=1,
        frame_duration_ms=20,
        max_frame_bytes=8_192,
        speech_start_threshold=1_200,
        speech_end_threshold=700,
        speech_start_frames=2,
        speech_end_frames=5,
        pre_roll_frames=5,
        max_utterance_duration_ms=30_000,
        max_utterance_bytes=1024 * 1024,
        asr_timeout_ms=30_000,
        max_pending_utterances=2,
        max_transcript_chars=32_000,
        barge_in_enabled=True,
    )


def test_voice_settings_load_from_nested_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "ENABLED": "false",
        "SAMPLE_RATE_HZ": "8000",
        "CHANNELS": "1",
        "FRAME_DURATION_MS": "10",
        "MAX_FRAME_BYTES": "160",
        "SPEECH_START_THRESHOLD": "950",
        "SPEECH_END_THRESHOLD": "500",
        "SPEECH_START_FRAMES": "3",
        "SPEECH_END_FRAMES": "7",
        "PRE_ROLL_FRAMES": "4",
        "MAX_UTTERANCE_DURATION_MS": "15000",
        "MAX_UTTERANCE_BYTES": "500000",
        "ASR_TIMEOUT_MS": "20000",
        "MAX_PENDING_UTTERANCES": "3",
        "MAX_TRANSCRIPT_CHARS": "12000",
        "BARGE_IN_ENABLED": "false",
    }
    for name, value in values.items():
        monkeypatch.setenv(f"EEVEETUBER_VOICE__{name}", value)

    settings = AppSettings(_env_file=None)

    assert settings.voice == VoiceInputSettings(
        enabled=False,
        sample_rate_hz=8_000,
        channels=1,
        frame_duration_ms=10,
        max_frame_bytes=160,
        speech_start_threshold=950,
        speech_end_threshold=500,
        speech_start_frames=3,
        speech_end_frames=7,
        pre_roll_frames=4,
        max_utterance_duration_ms=15_000,
        max_utterance_bytes=500_000,
        asr_timeout_ms=20_000,
        max_pending_utterances=3,
        max_transcript_chars=12_000,
        barge_in_enabled=False,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sample_rate_hz", 7_999),
        ("sample_rate_hz", 48_001),
        ("channels", 2),
        ("frame_duration_ms", 9),
        ("frame_duration_ms", 101),
        ("max_frame_bytes", 1),
        ("max_frame_bytes", 1024 * 1024 + 1),
        ("speech_start_threshold", 0),
        ("speech_start_threshold", 32_768),
        ("speech_end_threshold", 0),
        ("speech_end_threshold", 32_768),
        ("speech_start_frames", 0),
        ("speech_start_frames", 101),
        ("speech_end_frames", 0),
        ("speech_end_frames", 501),
        ("pre_roll_frames", -1),
        ("pre_roll_frames", 501),
        ("max_utterance_duration_ms", 99),
        ("max_utterance_duration_ms", 120_001),
        ("max_utterance_bytes", 1),
        ("max_utterance_bytes", 512 * 1024 * 1024 + 1),
        ("asr_timeout_ms", 99),
        ("asr_timeout_ms", 600_001),
        ("max_pending_utterances", 0),
        ("max_pending_utterances", 9),
        ("max_transcript_chars", 0),
        ("max_transcript_chars", 32_001),
    ],
)
def test_voice_settings_reject_invalid_field_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        VoiceInputSettings(**{field: value})


def test_voice_settings_accept_inclusive_bounds() -> None:
    settings = VoiceInputSettings(
        sample_rate_hz=8_000,
        frame_duration_ms=10,
        max_frame_bytes=160,
        speech_start_threshold=32_767,
        speech_end_threshold=1,
        speech_start_frames=100,
        speech_end_frames=500,
        pre_roll_frames=0,
        max_utterance_duration_ms=1_000,
        max_utterance_bytes=16_000,
        asr_timeout_ms=600_000,
        max_pending_utterances=8,
        max_transcript_chars=1,
    )

    assert settings.max_frame_bytes == 160
    assert settings.max_utterance_bytes == 16_000


def test_voice_settings_require_a_complete_pcm_frame() -> None:
    with pytest.raises(ValidationError, match="one complete 16-bit PCM frame"):
        VoiceInputSettings(sample_rate_hz=44_100, frame_duration_ms=25, max_frame_bytes=2_204)


def test_voice_settings_require_utterance_capacity_for_at_least_one_frame() -> None:
    with pytest.raises(ValidationError, match="at least max_frame_bytes"):
        VoiceInputSettings(max_frame_bytes=10_000, max_utterance_bytes=9_999)


def test_voice_settings_require_capacity_to_reach_speech_start() -> None:
    with pytest.raises(ValidationError, match="must hold the configured speech_start_frames"):
        VoiceInputSettings(
            frame_duration_ms=10,
            speech_start_frames=3,
            max_frame_bytes=320,
            max_utterance_bytes=959,
        )

    with pytest.raises(ValidationError, match="must span the configured speech_start_frames"):
        VoiceInputSettings(
            frame_duration_ms=100,
            speech_start_frames=2,
            max_utterance_duration_ms=199,
        )


def test_voice_settings_require_end_threshold_not_above_start_threshold() -> None:
    with pytest.raises(ValidationError, match="must not exceed"):
        VoiceInputSettings(speech_start_threshold=500, speech_end_threshold=501)


def test_process_settings_reject_voice_utterances_larger_than_asr_input_limit() -> None:
    with pytest.raises(ValidationError, match="cannot exceed ASR"):
        AppSettings(
            voice=VoiceInputSettings(max_utterance_bytes=2_000_000),
            asr=AsrAdapterSettings(max_input_pcm_bytes=1_000_000),
            _env_file=None,
        )
