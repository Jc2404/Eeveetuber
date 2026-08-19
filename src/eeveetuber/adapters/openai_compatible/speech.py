"""Clean-room OpenAI-compatible streaming text-to-speech adapter.

The adapter implements the public ``POST /audio/speech`` contract using httpx
directly.  It does not depend on an OpenAI SDK or on any reviewed VTuber source.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Awaitable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum

import httpx

from eeveetuber.dialogue.types import AudioChunk, UtteranceSegment
from eeveetuber.runtime.cancellation import CancellationToken


class SpeechAudioFormat(StrEnum):
    MP3 = "mp3"
    OPUS = "opus"
    AAC = "aac"
    FLAC = "flac"
    WAV = "wav"
    PCM = "pcm"


_MEDIA_TYPES = {
    SpeechAudioFormat.MP3: "audio/mpeg",
    SpeechAudioFormat.OPUS: "audio/ogg",
    SpeechAudioFormat.AAC: "audio/aac",
    SpeechAudioFormat.FLAC: "audio/flac",
    SpeechAudioFormat.WAV: "audio/wav",
    SpeechAudioFormat.PCM: "audio/pcm",
}


@dataclass(frozen=True, slots=True)
class OpenAICompatibleSpeechConfig:
    """Connection and declared output contract for one compatible endpoint.

    ``sample_rate_hz`` is transport metadata and must match the configured
    endpoint's actual output.  Set ``send_sample_rate=True`` only for local or
    third-party compatible servers that accept the non-standard request field;
    the OpenAI endpoint derives its rate from the selected response format.
    """

    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = field(default=None, repr=False)
    model: str = "gpt-4o-mini-tts"
    voice: str = "alloy"
    response_format: SpeechAudioFormat = SpeechAudioFormat.PCM
    sample_rate_hz: int = 24_000
    timeout_seconds: float = 30.0
    connect_timeout_seconds: float = 5.0
    chunk_size_bytes: int = 8_192
    max_response_bytes: int = 64 * 1024 * 1024
    send_sample_rate: bool = False
    use_segment_instructions: bool = False
    pcm_channels: int = 1
    pcm_sample_width_bytes: int = 2

    def __post_init__(self) -> None:
        normalized_url = self.base_url.rstrip("/")
        if not normalized_url.startswith(("http://", "https://")):
            raise ValueError("base_url must use http or https")
        if not self.model.strip() or not self.voice.strip():
            raise ValueError("model and voice cannot be blank")
        if self.api_key is not None and not self.api_key.strip():
            raise ValueError("api_key cannot be blank; use None for local endpoints")
        if self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.connect_timeout_seconds <= 0:
            raise ValueError("connect_timeout_seconds must be positive")
        if not 1 <= self.chunk_size_bytes <= 1024 * 1024:
            raise ValueError("chunk_size_bytes must be between 1 and 1048576")
        if self.max_response_bytes < self.chunk_size_bytes:
            raise ValueError("max_response_bytes cannot be smaller than chunk_size_bytes")
        if self.pcm_channels <= 0 or self.pcm_sample_width_bytes <= 0:
            raise ValueError("PCM channel count and sample width must be positive")
        object.__setattr__(self, "base_url", normalized_url)

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/audio/speech"

    @property
    def media_type(self) -> str:
        return _MEDIA_TYPES[self.response_format]


class SpeechAdapterError(RuntimeError):
    """Base for normalized, secret-safe provider failures."""


class SpeechAdapterClosed(SpeechAdapterError):
    pass


class SpeechTimeoutError(SpeechAdapterError):
    pass


class SpeechTransportError(SpeechAdapterError):
    pass


class SpeechProtocolError(SpeechAdapterError):
    pass


class SpeechHTTPError(SpeechAdapterError):
    def __init__(self, status_code: int, *, request_id: str | None = None) -> None:
        self.status_code = status_code
        self.request_id = request_id
        suffix = f" (request_id={request_id})" if request_id else ""
        super().__init__(f"speech provider returned HTTP {status_code}{suffix}")


class OpenAICompatibleSpeechSynthesizer:
    """Stream normalized, bounded audio chunks from an OpenAI-compatible server."""

    def __init__(
        self,
        config: OpenAICompatibleSpeechConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                config.timeout_seconds,
                connect=config.connect_timeout_seconds,
            )
        )
        self._closed = False
        self._state_lock = asyncio.Lock()
        self._active_responses: set[httpx.Response] = set()

    async def __aenter__(self) -> OpenAICompatibleSpeechSynthesizer:
        self._ensure_open()
        return self

    async def __aexit__(
        self,
        _exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> None:
        await self.aclose()

    async def synthesize(
        self,
        segment: UtteranceSegment,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AudioChunk]:
        """Yield ordered bytes and close the response on cancellation/generator close."""

        self._ensure_open()
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        request = self._client.build_request(
            "POST",
            self.config.endpoint,
            headers=self._headers(),
            json=self._request_body(segment),
            timeout=httpx.Timeout(
                self.config.timeout_seconds,
                connect=self.config.connect_timeout_seconds,
            ),
        )
        try:
            response = await _await_or_cancel(
                self._client.send(request, stream=True),
                cancellation,
            )
        except httpx.TimeoutException:
            raise SpeechTimeoutError("speech request timed out") from None
        except httpx.HTTPError as error:
            raise SpeechTransportError(
                f"speech transport failed ({type(error).__name__})"
            ) from None

        registered = False
        try:
            async with self._state_lock:
                if self._closed:
                    raise SpeechAdapterClosed("speech adapter is closed")
                self._active_responses.add(response)
                registered = True

            if not 200 <= response.status_code < 300:
                raise SpeechHTTPError(
                    response.status_code,
                    request_id=_safe_request_id(response.headers.get("x-request-id")),
                )
            content_type = response.headers.get("content-type", "").lower()
            if content_type.startswith("application/json"):
                raise SpeechProtocolError("speech provider returned JSON instead of audio")

            chunk_index = 0
            pending: bytes | None = None
            total_bytes = 0
            try:
                async for audio in _bounded_response_bytes(
                    response,
                    chunk_size=self.config.chunk_size_bytes,
                    cancellation=cancellation,
                ):
                    total_bytes += len(audio)
                    if total_bytes > self.config.max_response_bytes:
                        raise SpeechProtocolError("speech response exceeded configured byte limit")
                    if pending is not None:
                        yield self._audio_chunk(
                            segment,
                            chunk_index=chunk_index,
                            audio=pending,
                            is_final=False,
                        )
                        chunk_index += 1
                    pending = audio
            except httpx.TimeoutException:
                raise SpeechTimeoutError("speech response timed out") from None
            except httpx.HTTPError as error:
                if self._closed:
                    raise SpeechAdapterClosed("speech adapter closed during response") from None
                raise SpeechTransportError(
                    f"speech stream failed ({type(error).__name__})"
                ) from None

            if pending is None:
                raise SpeechProtocolError("speech provider returned an empty audio response")
            yield self._audio_chunk(
                segment,
                chunk_index=chunk_index,
                audio=pending,
                is_final=True,
            )
        finally:
            if registered:
                async with self._state_lock:
                    self._active_responses.discard(response)
            await response.aclose()

    async def aclose(self) -> None:
        """Close active response streams and, when owned, the HTTP client."""

        async with self._state_lock:
            if self._closed:
                return
            self._closed = True
            active = tuple(self._active_responses)
            self._active_responses.clear()
        for response in active:
            await response.aclose()
        if self._owns_client:
            await self._client.aclose()

    def _ensure_open(self) -> None:
        if self._closed:
            raise SpeechAdapterClosed("speech adapter is closed")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": self.config.media_type,
            "Content-Type": "application/json",
            "User-Agent": "eeveetuber/0.1",
        }
        if self.config.api_key is not None:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _request_body(self, segment: UtteranceSegment) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self.config.model,
            "input": segment.speakable_text,
            "voice": self.config.voice,
            "response_format": self.config.response_format.value,
        }
        if self.config.send_sample_rate:
            body["sample_rate"] = self.config.sample_rate_hz
        if self.config.use_segment_instructions:
            instructions = _segment_instructions(segment)
            if instructions:
                body["instructions"] = instructions
        return body

    def _audio_chunk(
        self,
        segment: UtteranceSegment,
        *,
        chunk_index: int,
        audio: bytes,
        is_final: bool,
    ) -> AudioChunk:
        return AudioChunk(
            segment_id=segment.segment_id,
            sequence=segment.sequence,
            chunk_index=chunk_index,
            audio=audio,
            media_type=self.config.media_type,
            sample_rate_hz=self.config.sample_rate_hz,
            is_final=is_final,
            duration_ms=_duration_ms(self.config, len(audio)),
        )


async def _bounded_response_bytes(
    response: httpx.Response,
    *,
    chunk_size: int,
    cancellation: CancellationToken | None,
) -> AsyncIterator[bytes]:
    iterator = response.aiter_bytes(chunk_size=chunk_size).__aiter__()
    while True:
        try:
            value = await _await_or_cancel(anext(iterator), cancellation)
        except StopAsyncIteration:
            return
        if not value:
            continue
        for offset in range(0, len(value), chunk_size):
            yield value[offset : offset + chunk_size]


async def _await_or_cancel[ResultT](
    awaitable: Awaitable[ResultT],
    cancellation: CancellationToken | None,
) -> ResultT:
    if cancellation is None:
        return await awaitable
    cancellation.raise_if_cancelled()
    operation = asyncio.ensure_future(awaitable)
    cancelled = asyncio.create_task(cancellation.wait_cancelled())
    try:
        done, _pending = await asyncio.wait(
            {operation, cancelled},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancelled in done:
            reason = cancelled.result()
            operation.cancel()
            with suppress(asyncio.CancelledError):
                await operation
            raise asyncio.CancelledError(reason)
        return operation.result()
    finally:
        if not operation.done():
            operation.cancel()
            with suppress(asyncio.CancelledError):
                await operation
        cancelled.cancel()
        with suppress(asyncio.CancelledError):
            await cancelled


def _duration_ms(config: OpenAICompatibleSpeechConfig, byte_count: int) -> int | None:
    if config.response_format is not SpeechAudioFormat.PCM:
        return None
    bytes_per_second = (
        config.sample_rate_hz * config.pcm_channels * config.pcm_sample_width_bytes
    )
    return round(byte_count * 1_000 / bytes_per_second)


def _segment_instructions(segment: UtteranceSegment) -> str | None:
    parts = []
    if segment.affect:
        parts.append(f"Affect: {segment.affect.strip()}")
    if segment.delivery:
        parts.append(f"Delivery: {segment.delivery.strip()}")
    return ". ".join(part for part in parts if part) or None


def _safe_request_id(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "", value)[:128]
    return cleaned or None
