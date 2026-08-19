from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator
from uuid import UUID

import pytest

from eeveetuber.adapters.fake import FakeSpeechRecognizer
from eeveetuber.application import VoiceInputCoordinator, VoiceInputPolicy
from eeveetuber.media import (
    AsrFinal,
    AsrPartial,
    AsrStreamEvent,
    EnergyVadConfig,
    PcmFormat,
    PcmFrame,
    PcmUtterance,
    VadSpeechStarted,
)
from eeveetuber.runtime import CancellationToken

STREAM_ID = UUID("00000000-0000-0000-0000-000000000071")


class RecordingVoiceSink:
    def __init__(self) -> None:
        self.captures: list[tuple[str, UUID]] = []
        self.speech: list[VadSpeechStarted] = []
        self.partials: list[AsrPartial] = []
        self.finals: list[AsrFinal] = []
        self.failures: list[tuple[UUID, str]] = []
        self.final_ready = asyncio.Event()

    async def voice_capture_started(self, stream_id: UUID, pcm_format: PcmFormat) -> None:
        assert pcm_format == PcmFormat(16_000)
        self.captures.append(("started", stream_id))

    async def voice_capture_stopped(self, stream_id: UUID, *, reason: str) -> None:
        self.captures.append((reason, stream_id))

    async def voice_speech_started(
        self,
        event: VadSpeechStarted,
        *,
        barge_in: bool,
    ) -> None:
        assert barge_in
        self.speech.append(event)

    async def voice_transcript_partial(self, event: AsrPartial) -> None:
        self.partials.append(event)

    async def voice_transcript_final(self, event: AsrFinal) -> None:
        self.finals.append(event)
        self.final_ready.set()

    async def voice_recognition_failed(
        self,
        utterance_id: UUID,
        *,
        error_type: str,
    ) -> None:
        self.failures.append((utterance_id, error_type))
        self.final_ready.set()


def _policy(**overrides: object) -> VoiceInputPolicy:
    values: dict[str, object] = {
        "enabled": True,
        "pcm_format": PcmFormat(16_000),
        "frame_duration_ms": 10,
        "max_frame_bytes": 1_024,
        "vad": EnergyVadConfig(
            speech_start_threshold=1_000,
            speech_end_threshold=500,
            speech_start_frames=1,
            speech_end_frames=1,
            pre_roll_frames=0,
            max_utterance_duration_ms=1_000,
            max_utterance_bytes=8_192,
        ),
        "asr_timeout_ms": 1_000,
        "max_pending_utterances": 1,
        "max_transcript_chars": 100,
        "barge_in_enabled": True,
    }
    values.update(overrides)
    return VoiceInputPolicy(**values)  # type: ignore[arg-type]


def _frame(sequence: int, amplitude: int) -> PcmFrame:
    samples = 160
    return PcmFrame(
        stream_id=STREAM_ID,
        sequence=sequence,
        captured_at_monotonic_ns=sequence * 10_000_000,
        format=PcmFormat(16_000),
        pcm=struct.pack(f"<{samples}h", *([amplitude] * samples)),
    )


@pytest.mark.asyncio
async def test_vad_to_asr_lane_emits_partials_and_one_final_without_retaining_pcm() -> None:
    sink = RecordingVoiceSink()
    recognizer = FakeSpeechRecognizer("hello Eevee", partials=("hello",))
    coordinator = VoiceInputCoordinator(recognizer, sink, _policy())

    await coordinator.start_stream(STREAM_ID, PcmFormat(16_000))
    await coordinator.process_frame(_frame(0, 2_000))
    await coordinator.process_frame(_frame(1, 0))
    await asyncio.wait_for(sink.final_ready.wait(), timeout=1)
    await coordinator.finish_stream(STREAM_ID, reason="operator_requested")
    await coordinator.close()

    assert [entry[0] for entry in sink.captures] == ["started", "operator_requested"]
    assert len(sink.speech) == 1
    assert [partial.text for partial in sink.partials] == ["hello"]
    assert [final.text for final in sink.finals] == ["hello Eevee"]
    assert not sink.failures
    assert len(recognizer.requests) == 1
    assert not hasattr(recognizer.requests[0], "pcm")


class NoFinalRecognizer:
    async def recognize(
        self,
        utterance: object,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AsrStreamEvent]:
        del utterance, cancellation
        if False:  # pragma: no cover - declares this as an async generator
            yield


class EventAfterFinalRecognizer:
    async def recognize(
        self,
        utterance: PcmUtterance,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AsrStreamEvent]:
        del cancellation
        yield AsrFinal(utterance.utterance_id, "must not escape")
        yield AsrPartial(utterance.utterance_id, 0, "late")


class SupersededRecognizer:
    def __init__(self) -> None:
        self.calls = 0
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def recognize(
        self,
        utterance: PcmUtterance,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AsrStreamEvent]:
        del cancellation
        self.calls += 1
        if self.calls == 1:
            self.first_started.set()
            await self.release_first.wait()
            yield AsrFinal(utterance.utterance_id, "stale")
        else:
            yield AsrFinal(utterance.utterance_id, "new")


@pytest.mark.asyncio
async def test_missing_asr_final_becomes_bounded_failure() -> None:
    sink = RecordingVoiceSink()
    coordinator = VoiceInputCoordinator(NoFinalRecognizer(), sink, _policy())  # type: ignore[arg-type]

    await coordinator.start_stream(STREAM_ID, PcmFormat(16_000))
    await coordinator.process_frame(_frame(0, 2_000))
    await coordinator.process_frame(_frame(1, 0))
    await asyncio.wait_for(sink.final_ready.wait(), timeout=1)
    await coordinator.close()

    assert not sink.finals
    assert sink.failures[0][1] == "VoiceRecognitionProtocolError"


@pytest.mark.asyncio
async def test_final_does_not_escape_until_provider_stream_is_proven_terminal() -> None:
    sink = RecordingVoiceSink()
    coordinator = VoiceInputCoordinator(EventAfterFinalRecognizer(), sink, _policy())

    await coordinator.start_stream(STREAM_ID, PcmFormat(16_000))
    await coordinator.process_frame(_frame(0, 2_000))
    await coordinator.process_frame(_frame(1, 0))
    await asyncio.wait_for(sink.final_ready.wait(), timeout=1)
    await coordinator.close()

    assert not sink.finals
    assert sink.failures[0][1] == "VoiceRecognitionProtocolError"


@pytest.mark.asyncio
async def test_new_speech_supersedes_inflight_asr_and_only_latest_final_escapes() -> None:
    sink = RecordingVoiceSink()
    recognizer = SupersededRecognizer()
    coordinator = VoiceInputCoordinator(recognizer, sink, _policy())

    await coordinator.start_stream(STREAM_ID, PcmFormat(16_000))
    await coordinator.process_frame(_frame(0, 2_000))
    await coordinator.process_frame(_frame(1, 0))
    await asyncio.wait_for(recognizer.first_started.wait(), timeout=1)
    await coordinator.process_frame(_frame(2, 2_000))
    await coordinator.process_frame(_frame(3, 0))
    await asyncio.wait_for(sink.final_ready.wait(), timeout=1)
    await coordinator.close()

    assert [final.text for final in sink.finals] == ["new"]
    assert not sink.failures


@pytest.mark.asyncio
async def test_capture_contract_rejects_wrong_format_and_oversized_frames() -> None:
    sink = RecordingVoiceSink()
    coordinator = VoiceInputCoordinator(FakeSpeechRecognizer(), sink, _policy())

    with pytest.raises(RuntimeError, match="format"):
        await coordinator.start_stream(STREAM_ID, PcmFormat(24_000))

    await coordinator.start_stream(STREAM_ID, PcmFormat(16_000))
    oversized = PcmFrame(
        stream_id=STREAM_ID,
        sequence=0,
        captured_at_monotonic_ns=0,
        format=PcmFormat(16_000),
        pcm=bytes(1_026),
    )
    with pytest.raises(RuntimeError, match="byte limit"):
        await coordinator.process_frame(oversized)
    await coordinator.close()


@pytest.mark.asyncio
async def test_invalid_frame_order_is_normalized_to_capture_state_error() -> None:
    sink = RecordingVoiceSink()
    coordinator = VoiceInputCoordinator(FakeSpeechRecognizer(), sink, _policy())

    await coordinator.start_stream(STREAM_ID, PcmFormat(16_000))
    await coordinator.process_frame(_frame(0, 2_000))
    with pytest.raises(RuntimeError, match="order or timing"):
        await coordinator.process_frame(_frame(0, 0))
    await coordinator.close()


@pytest.mark.asyncio
async def test_frame_duration_must_match_advertised_vad_cadence() -> None:
    sink = RecordingVoiceSink()
    coordinator = VoiceInputCoordinator(FakeSpeechRecognizer(), sink, _policy())
    short = PcmFrame(
        stream_id=STREAM_ID,
        sequence=0,
        captured_at_monotonic_ns=0,
        format=PcmFormat(16_000),
        pcm=bytes(160),
    )

    await coordinator.start_stream(STREAM_ID, PcmFormat(16_000))
    with pytest.raises(RuntimeError, match="duration"):
        await coordinator.process_frame(short)
    await coordinator.close()
