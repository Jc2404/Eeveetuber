"""Small deterministic energy VAD for session-owned live capture pipelines."""

from __future__ import annotations

import struct
from collections import deque
from dataclasses import dataclass
from uuid import UUID, uuid5

from eeveetuber.media.types import PcmFormat, PcmFrame, PcmUtterance, UtteranceEndReason


@dataclass(frozen=True, slots=True)
class EnergyVadConfig:
    """All memory and utterance bounds for :class:`EnergyVoiceActivityDetector`."""

    speech_start_threshold: int = 1_200
    speech_end_threshold: int = 700
    speech_start_frames: int = 2
    speech_end_frames: int = 5
    pre_roll_frames: int = 5
    max_utterance_duration_ms: int = 30_000
    max_utterance_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        integer_fields = (
            ("speech_start_threshold", self.speech_start_threshold, 1, 32_767),
            ("speech_end_threshold", self.speech_end_threshold, 1, 32_767),
            ("speech_start_frames", self.speech_start_frames, 1, None),
            ("speech_end_frames", self.speech_end_frames, 1, None),
            ("pre_roll_frames", self.pre_roll_frames, 0, None),
            ("max_utterance_duration_ms", self.max_utterance_duration_ms, 1, None),
            ("max_utterance_bytes", self.max_utterance_bytes, 2, None),
        )
        for name, value, minimum, maximum in integer_fields:
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum or (
                maximum is not None and value > maximum
            ):
                suffix = f" and {maximum}" if maximum is not None else ""
                raise ValueError(f"{name} must be an integer between {minimum}{suffix}")
        if self.speech_end_threshold > self.speech_start_threshold:
            raise ValueError("speech_end_threshold cannot exceed speech_start_threshold")

    @property
    def max_utterance_duration_ns(self) -> int:
        return self.max_utterance_duration_ms * 1_000_000


@dataclass(frozen=True, slots=True)
class VadSpeechStarted:
    """Normalized transition from idle audio to a confirmed speech utterance."""

    stream_id: UUID
    utterance_id: UUID
    at_monotonic_ns: int
    trigger_sequence: int
    pre_roll_frame_count: int


@dataclass(frozen=True, slots=True)
class VadSpeechEnded:
    """Normalized terminal VAD event carrying its bounded in-memory utterance."""

    stream_id: UUID
    utterance_id: UUID
    at_monotonic_ns: int
    reason: UtteranceEndReason
    utterance: PcmUtterance


type VadEvent = VadSpeechStarted | VadSpeechEnded


class EnergyVoiceActivityDetector:
    """Per-session bounded VAD with pre-roll and threshold hysteresis.

    The implementation has no background task or global state.  One owning
    session feeds ordered frames and calls :meth:`finish_stream` when capture
    stops.  Energy comparisons use integer mean-square values, keeping decisions
    reproducible across platforms.
    """

    def __init__(self, config: EnergyVadConfig | None = None) -> None:
        self.config = config or EnergyVadConfig()
        self._stream_id: UUID | None = None
        self._format: PcmFormat | None = None
        self._last_sequence: int | None = None
        self._last_end_ns: int | None = None
        self._history: deque[PcmFrame] = deque()
        self._history_bytes = 0
        self._history_duration_ns = 0
        self._candidate_count = 0
        self._candidate_bytes = 0
        self._candidate_duration_ns = 0
        self._candidate_start_ns: int | None = None
        self._candidate_start_sequence: int | None = None
        self._active_frames: list[PcmFrame] | None = None
        self._active_bytes = 0
        self._active_duration_ns = 0
        self._utterance_id: UUID | None = None
        self._speech_started_at_ns: int | None = None
        self._silence_count = 0
        self._silence_started_at_ns: int | None = None

    @property
    def speech_active(self) -> bool:
        return self._active_frames is not None

    @property
    def buffered_bytes(self) -> int:
        """Bytes currently retained, exposed for bound assertions and metrics."""

        return self._active_bytes if self.speech_active else self._history_bytes

    def process(self, frame: PcmFrame) -> tuple[VadEvent, ...]:
        """Consume one ordered frame and return zero or more state transitions."""

        self._validate_stream_frame(frame)
        self._last_sequence = frame.sequence
        self._last_end_ns = frame.end_monotonic_ns
        if self.speech_active:
            return self._process_active(frame)
        return self._process_idle(frame)

    def finish_stream(self) -> VadSpeechEnded | None:
        """Flush an active utterance, discard idle pre-roll, and release stream state."""

        event = (
            self._finish_utterance(UtteranceEndReason.STREAM_ENDED)
            if self.speech_active
            else None
        )
        self._clear_stream()
        return event

    def _validate_stream_frame(self, frame: PcmFrame) -> None:
        if len(frame.pcm) > self.config.max_utterance_bytes:
            raise ValueError("one PCM frame exceeds max_utterance_bytes")
        if frame.duration_ns > self.config.max_utterance_duration_ns:
            raise ValueError("one PCM frame exceeds max_utterance_duration_ms")
        if self._stream_id is None:
            self._stream_id = frame.stream_id
            self._format = frame.format
        elif frame.stream_id != self._stream_id:
            raise ValueError("VAD instance cannot mix capture stream identities")
        elif frame.format != self._format:
            raise ValueError("PCM format cannot change within a capture stream")
        if self._last_sequence is not None and frame.sequence <= self._last_sequence:
            raise ValueError("PCM frame sequence must be strictly increasing")
        if self._last_end_ns is not None and frame.captured_at_monotonic_ns < self._last_end_ns:
            raise ValueError("PCM frame timestamps cannot overlap")

    def _process_idle(self, frame: PcmFrame) -> tuple[VadEvent, ...]:
        self._remember_idle_frame(frame)
        if self._is_above(frame, self.config.speech_start_threshold):
            projected_bytes = self._candidate_bytes + len(frame.pcm)
            projected_duration = self._candidate_duration_ns + frame.duration_ns
            if (
                projected_bytes > self.config.max_utterance_bytes
                or projected_duration > self.config.max_utterance_duration_ns
            ):
                self._reset_candidate()
            if self._candidate_count == 0:
                self._candidate_start_ns = frame.captured_at_monotonic_ns
                self._candidate_start_sequence = frame.sequence
            self._candidate_count += 1
            self._candidate_bytes += len(frame.pcm)
            self._candidate_duration_ns += frame.duration_ns
        else:
            self._reset_candidate()

        if self._candidate_count < self.config.speech_start_frames:
            return ()

        started = self._start_utterance(frame)
        limit_reason = self._reached_limit_reason()
        if limit_reason is None:
            return (started,)
        return (started, self._finish_utterance(limit_reason))

    def _process_active(self, frame: PcmFrame) -> tuple[VadEvent, ...]:
        projected_bytes = self._active_bytes + len(frame.pcm)
        projected_duration = self._active_duration_ns + frame.duration_ns
        if projected_bytes > self.config.max_utterance_bytes:
            ended = self._finish_utterance(UtteranceEndReason.MAX_BYTES)
            return (ended, *self._process_idle(frame))
        if projected_duration > self.config.max_utterance_duration_ns:
            ended = self._finish_utterance(UtteranceEndReason.MAX_DURATION)
            return (ended, *self._process_idle(frame))

        active_frames = self._require_active_frames()
        active_frames.append(frame)
        self._active_bytes = projected_bytes
        self._active_duration_ns = projected_duration

        if self._is_above(frame, self.config.speech_end_threshold):
            self._silence_count = 0
            self._silence_started_at_ns = None
        else:
            if self._silence_count == 0:
                self._silence_started_at_ns = frame.captured_at_monotonic_ns
            self._silence_count += 1

        limit_reason = self._reached_limit_reason()
        if limit_reason is not None:
            return (self._finish_utterance(limit_reason),)
        if self._silence_count >= self.config.speech_end_frames:
            return (self._finish_utterance(UtteranceEndReason.SILENCE),)
        return ()

    def _remember_idle_frame(self, frame: PcmFrame) -> None:
        self._history.append(frame)
        self._history_bytes += len(frame.pcm)
        self._history_duration_ns += frame.duration_ns
        max_frames = self.config.pre_roll_frames + self.config.speech_start_frames
        while (
            len(self._history) > max_frames
            or self._history_bytes > self.config.max_utterance_bytes
            or self._history_duration_ns > self.config.max_utterance_duration_ns
        ):
            removed = self._history.popleft()
            self._history_bytes -= len(removed.pcm)
            self._history_duration_ns -= removed.duration_ns

    def _start_utterance(self, trigger: PcmFrame) -> VadSpeechStarted:
        stream_id = self._require_stream_id()
        candidate_start_ns = self._require_candidate_start_ns()
        candidate_start_sequence = self._require_candidate_start_sequence()
        utterance_id = uuid5(stream_id, f"speech:{candidate_start_sequence}")
        pre_roll_count = sum(
            frame.sequence < candidate_start_sequence for frame in self._history
        )
        self._active_frames = list(self._history)
        self._active_bytes = self._history_bytes
        self._active_duration_ns = self._history_duration_ns
        self._utterance_id = utterance_id
        self._speech_started_at_ns = candidate_start_ns
        self._history.clear()
        self._history_bytes = 0
        self._history_duration_ns = 0
        self._reset_candidate()
        self._silence_count = 0
        self._silence_started_at_ns = None
        return VadSpeechStarted(
            stream_id=stream_id,
            utterance_id=utterance_id,
            at_monotonic_ns=candidate_start_ns,
            trigger_sequence=trigger.sequence,
            pre_roll_frame_count=pre_roll_count,
        )

    def _finish_utterance(self, reason: UtteranceEndReason) -> VadSpeechEnded:
        frames = tuple(self._require_active_frames())
        stream_id = self._require_stream_id()
        pcm_format = self._require_format()
        utterance_id = self._require_utterance_id()
        speech_start = self._require_speech_started_at_ns()
        speech_end = (
            self._silence_started_at_ns
            if reason is UtteranceEndReason.SILENCE and self._silence_started_at_ns is not None
            else frames[-1].end_monotonic_ns
        )
        utterance = PcmUtterance(
            utterance_id=utterance_id,
            stream_id=stream_id,
            format=pcm_format,
            frames=frames,
            speech_started_at_monotonic_ns=speech_start,
            speech_ended_at_monotonic_ns=speech_end,
            end_reason=reason,
        )
        event = VadSpeechEnded(
            stream_id=stream_id,
            utterance_id=utterance_id,
            at_monotonic_ns=speech_end,
            reason=reason,
            utterance=utterance,
        )
        self._clear_utterance()
        return event

    def _reached_limit_reason(self) -> UtteranceEndReason | None:
        if self._active_duration_ns >= self.config.max_utterance_duration_ns:
            return UtteranceEndReason.MAX_DURATION
        if self._active_bytes >= self.config.max_utterance_bytes:
            return UtteranceEndReason.MAX_BYTES
        return None

    @staticmethod
    def _is_above(frame: PcmFrame, threshold: int) -> bool:
        sample_count = len(frame.pcm) // 2
        square_sum = 0
        for unpacked in struct.iter_unpack("<h", frame.pcm):
            sample = int(unpacked[0])
            square_sum += sample * sample
        return square_sum >= threshold * threshold * sample_count

    def _reset_candidate(self) -> None:
        self._candidate_count = 0
        self._candidate_bytes = 0
        self._candidate_duration_ns = 0
        self._candidate_start_ns = None
        self._candidate_start_sequence = None

    def _clear_utterance(self) -> None:
        self._active_frames = None
        self._active_bytes = 0
        self._active_duration_ns = 0
        self._utterance_id = None
        self._speech_started_at_ns = None
        self._silence_count = 0
        self._silence_started_at_ns = None
        self._reset_candidate()

    def _clear_stream(self) -> None:
        self._clear_utterance()
        self._history.clear()
        self._history_bytes = 0
        self._history_duration_ns = 0
        self._stream_id = None
        self._format = None
        self._last_sequence = None
        self._last_end_ns = None

    def _require_stream_id(self) -> UUID:
        if self._stream_id is None:
            raise RuntimeError("VAD has no active stream")
        return self._stream_id

    def _require_format(self) -> PcmFormat:
        if self._format is None:
            raise RuntimeError("VAD has no active PCM format")
        return self._format

    def _require_active_frames(self) -> list[PcmFrame]:
        if self._active_frames is None:
            raise RuntimeError("VAD has no active utterance")
        return self._active_frames

    def _require_utterance_id(self) -> UUID:
        if self._utterance_id is None:
            raise RuntimeError("VAD has no active utterance identity")
        return self._utterance_id

    def _require_speech_started_at_ns(self) -> int:
        if self._speech_started_at_ns is None:
            raise RuntimeError("VAD has no speech start timestamp")
        return self._speech_started_at_ns

    def _require_candidate_start_ns(self) -> int:
        if self._candidate_start_ns is None:
            raise RuntimeError("VAD has no speech candidate timestamp")
        return self._candidate_start_ns

    def _require_candidate_start_sequence(self) -> int:
        if self._candidate_start_sequence is None:
            raise RuntimeError("VAD has no speech candidate sequence")
        return self._candidate_start_sequence


__all__ = [
    "EnergyVadConfig",
    "EnergyVoiceActivityDetector",
    "VadEvent",
    "VadSpeechEnded",
    "VadSpeechStarted",
]
