"""Bounded per-session voice ingress orchestration.

Raw PCM stays inside this realtime coordinator and the configured ASR adapter. Only
VAD transitions and normalized transcript events cross into the session actor.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from eeveetuber.dialogue.ports import AsyncCloseable
from eeveetuber.domain.events import JsonValue
from eeveetuber.media import (
    AsrFinal,
    AsrPartial,
    EnergyVadConfig,
    EnergyVoiceActivityDetector,
    PcmFormat,
    PcmFrame,
    PcmUtterance,
    SpeechRecognizer,
    VadSpeechEnded,
    VadSpeechStarted,
)
from eeveetuber.runtime import CancellationSource


class VoiceInputError(RuntimeError):
    """Base error for invalid capture lifecycle or ASR stream behavior."""


class VoiceCaptureStateError(VoiceInputError):
    pass


class VoiceRecognitionProtocolError(VoiceInputError):
    pass


@dataclass(frozen=True, slots=True)
class _RecognitionJob:
    speech_epoch: int
    utterance: PcmUtterance


@dataclass(frozen=True, slots=True)
class VoiceInputPolicy:
    """Application-owned bounds compiled from validated process settings."""

    enabled: bool
    pcm_format: PcmFormat
    frame_duration_ms: int
    max_frame_bytes: int
    vad: EnergyVadConfig
    asr_timeout_ms: int
    max_pending_utterances: int
    max_transcript_chars: int
    barge_in_enabled: bool

    def __post_init__(self) -> None:
        if self.frame_duration_ms < 1:
            raise ValueError("frame_duration_ms must be positive")
        if self.max_frame_bytes < self.pcm_format.bytes_per_sample_frame:
            raise ValueError("max_frame_bytes cannot be smaller than one PCM sample frame")
        if self.asr_timeout_ms < 1:
            raise ValueError("asr_timeout_ms must be positive")
        if self.max_pending_utterances < 1:
            raise ValueError("max_pending_utterances must be positive")
        if self.max_transcript_chars < 1:
            raise ValueError("max_transcript_chars must be positive")

    def public_config(self) -> dict[str, JsonValue]:
        """Return the non-sensitive capture contract advertised to the browser."""

        return {
            "enabled": self.enabled,
            "sample_rate_hz": self.pcm_format.sample_rate_hz,
            "channels": self.pcm_format.channels,
            "encoding": self.pcm_format.encoding.value,
            "frame_duration_ms": self.frame_duration_ms,
            "max_frame_bytes": self.max_frame_bytes,
            "barge_in_enabled": self.barge_in_enabled,
        }

    @property
    def expected_frame_sample_count(self) -> int:
        return (
            self.pcm_format.sample_rate_hz * self.frame_duration_ms + 999
        ) // 1_000


class VoiceInputSink(Protocol):
    """Low-frequency application callbacks implemented by ``ForegroundSession``."""

    async def voice_capture_started(self, stream_id: UUID, pcm_format: PcmFormat) -> None: ...

    async def voice_capture_stopped(self, stream_id: UUID, *, reason: str) -> None: ...

    async def voice_speech_started(
        self,
        event: VadSpeechStarted,
        *,
        barge_in: bool,
    ) -> None: ...

    async def voice_transcript_partial(self, event: AsrPartial) -> None: ...

    async def voice_transcript_final(self, event: AsrFinal) -> None: ...

    async def voice_recognition_failed(
        self,
        utterance_id: UUID,
        *,
        error_type: str,
    ) -> None: ...


class VoiceInputCoordinator:
    """Own one capture stream, one VAD, and a bounded sequential ASR lane."""

    def __init__(
        self,
        recognizer: SpeechRecognizer,
        sink: VoiceInputSink,
        policy: VoiceInputPolicy,
    ) -> None:
        self._recognizer = recognizer
        self._sink = sink
        self.policy = policy
        self._detector: EnergyVoiceActivityDetector | None = None
        self._stream_id: UUID | None = None
        self._utterances: asyncio.Queue[_RecognitionJob] = asyncio.Queue(
            maxsize=policy.max_pending_utterances
        )
        self._worker: asyncio.Task[None] | None = None
        self._active_recognition_task: asyncio.Task[None] | None = None
        self._active_asr: CancellationSource | None = None
        self._speech_epoch = 0
        self._active_utterance_epoch: int | None = None
        self._closed = False

    @property
    def stream_id(self) -> UUID | None:
        return self._stream_id

    @property
    def capture_active(self) -> bool:
        return self._stream_id is not None

    @property
    def pending_utterances(self) -> int:
        return self._utterances.qsize()

    async def start_stream(self, stream_id: UUID, pcm_format: PcmFormat) -> None:
        self._ensure_open()
        if not self.policy.enabled:
            raise VoiceCaptureStateError("voice input is disabled")
        if self._stream_id is not None:
            raise VoiceCaptureStateError("a voice capture stream is already active")
        if pcm_format != self.policy.pcm_format:
            raise VoiceCaptureStateError("capture PCM format does not match the server contract")
        self._stream_id = stream_id
        self._detector = EnergyVoiceActivityDetector(self.policy.vad)
        self._ensure_worker()
        await self._sink.voice_capture_started(stream_id, pcm_format)

    async def process_frame(self, frame: PcmFrame) -> None:
        self._ensure_open()
        detector = self._require_detector()
        if frame.stream_id != self._stream_id:
            raise VoiceCaptureStateError("PCM frame belongs to a different capture stream")
        if frame.format != self.policy.pcm_format:
            raise VoiceCaptureStateError("PCM frame format changed during capture")
        if len(frame.pcm) > self.policy.max_frame_bytes:
            raise VoiceCaptureStateError("PCM frame exceeds the configured byte limit")
        if frame.sample_frame_count != self.policy.expected_frame_sample_count:
            raise VoiceCaptureStateError("PCM frame duration does not match the server contract")
        try:
            events = detector.process(frame)
        except (TypeError, ValueError) as error:
            raise VoiceCaptureStateError("PCM frame order or timing is invalid") from error
        for event in events:
            if isinstance(event, VadSpeechStarted):
                self._speech_epoch += 1
                self._active_utterance_epoch = self._speech_epoch
                if self._active_asr is not None:
                    self._active_asr.cancel_current("superseded by newer speech")
                if self._active_recognition_task is not None:
                    self._active_recognition_task.cancel()
                await self._sink.voice_speech_started(
                    event,
                    barge_in=self.policy.barge_in_enabled,
                )
            else:
                await self._enqueue_utterance(event, self._take_utterance_epoch())

    async def finish_stream(self, stream_id: UUID, *, reason: str) -> None:
        self._ensure_open()
        if not reason.strip():
            raise ValueError("voice capture stop reason cannot be blank")
        if stream_id != self._stream_id:
            raise VoiceCaptureStateError("voice capture stop does not match the active stream")
        detector = self._require_detector()
        ended = detector.finish_stream()
        self._detector = None
        self._stream_id = None
        if ended is not None:
            await self._enqueue_utterance(ended, self._take_utterance_epoch())
        await self._sink.voice_capture_stopped(stream_id, reason=reason)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._detector = None
        self._stream_id = None
        self._active_utterance_epoch = None
        if self._active_asr is not None:
            self._active_asr.close("voice input closed")
        if self._active_recognition_task is not None:
            self._active_recognition_task.cancel()
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        while not self._utterances.empty():
            self._utterances.get_nowait()
            self._utterances.task_done()
        if isinstance(self._recognizer, AsyncCloseable):
            await self._recognizer.aclose()

    async def _enqueue_utterance(
        self,
        ended: VadSpeechEnded,
        speech_epoch: int,
    ) -> None:
        try:
            self._utterances.put_nowait(_RecognitionJob(speech_epoch, ended.utterance))
        except asyncio.QueueFull:
            await self._report_failure(
                ended.utterance_id,
                error_type="VoiceInputBackpressure",
            )

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_asr(), name="voice-asr")

    async def _run_asr(self) -> None:
        while True:
            job = await self._utterances.get()
            try:
                if job.speech_epoch == self._speech_epoch:
                    recognition_task = asyncio.create_task(
                        self._recognize(job),
                        name=f"voice-asr:{job.utterance.utterance_id}",
                    )
                    self._active_recognition_task = recognition_task
                    await recognition_task
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if self._closed or (task is not None and task.cancelling()):
                    raise
                if job.speech_epoch == self._speech_epoch:
                    await self._report_failure(
                        job.utterance.utterance_id,
                        error_type="VoiceRecognitionCancelled",
                    )
            except TimeoutError:
                if job.speech_epoch == self._speech_epoch:
                    await self._report_failure(
                        job.utterance.utterance_id,
                        error_type="VoiceRecognitionTimeout",
                    )
            except Exception as error:
                if job.speech_epoch == self._speech_epoch:
                    await self._report_failure(
                        job.utterance.utterance_id,
                        error_type=type(error).__name__,
                    )
            finally:
                self._active_recognition_task = None
                self._active_asr = None
                self._utterances.task_done()

    async def _recognize(self, job: _RecognitionJob) -> None:
        utterance = job.utterance
        cancellation = CancellationSource()
        self._active_asr = cancellation
        final_seen = False
        final: AsrFinal | None = None
        last_partial_revision = -1
        try:
            async with asyncio.timeout(self.policy.asr_timeout_ms / 1_000):
                async for event in self._recognizer.recognize(
                    utterance,
                    cancellation=cancellation.token(),
                ):
                    if event.utterance_id != utterance.utterance_id:
                        raise VoiceRecognitionProtocolError(
                            "ASR event identity does not match the submitted utterance"
                        )
                    if job.speech_epoch != self._speech_epoch:
                        return
                    if final_seen:
                        raise VoiceRecognitionProtocolError("ASR emitted an event after its final")
                    if len(event.text) > self.policy.max_transcript_chars:
                        raise VoiceRecognitionProtocolError(
                            "ASR transcript exceeds the configured character limit"
                        )
                    if isinstance(event, AsrPartial):
                        if event.revision <= last_partial_revision:
                            raise VoiceRecognitionProtocolError(
                                "ASR partial revisions must be strictly increasing"
                            )
                        last_partial_revision = event.revision
                        await self._sink.voice_transcript_partial(event)
                    else:
                        final_seen = True
                        final = event
            if final is None:
                raise VoiceRecognitionProtocolError("ASR stream ended without a final transcript")
            if job.speech_epoch == self._speech_epoch:
                await self._sink.voice_transcript_final(final)
        except TimeoutError:
            cancellation.cancel_current("voice recognition timed out")
            raise
        finally:
            cancellation.close("voice recognition finished")

    async def _report_failure(self, utterance_id: UUID, *, error_type: str) -> None:
        try:
            await self._sink.voice_recognition_failed(
                utterance_id,
                error_type=error_type,
            )
        except Exception:
            # A stopped/failed session cannot consume diagnostics; keep the ASR worker alive
            # long enough for orderly coordinator shutdown instead of retaining queued PCM.
            return

    def _take_utterance_epoch(self) -> int:
        speech_epoch = self._active_utterance_epoch
        self._active_utterance_epoch = None
        if speech_epoch is None:
            raise VoiceCaptureStateError("VAD ended speech without a matching start")
        return speech_epoch

    def _require_detector(self) -> EnergyVoiceActivityDetector:
        if self._detector is None or self._stream_id is None:
            raise VoiceCaptureStateError("no voice capture stream is active")
        return self._detector

    def _ensure_open(self) -> None:
        if self._closed:
            raise VoiceCaptureStateError("voice input coordinator is closed")


__all__ = [
    "VoiceCaptureStateError",
    "VoiceInputCoordinator",
    "VoiceInputError",
    "VoiceInputPolicy",
    "VoiceInputSink",
    "VoiceRecognitionProtocolError",
]
