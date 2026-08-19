"""Clean-room OpenAI-compatible speech-to-text adapter.

Bounded PCM is wrapped as WAV entirely in memory and sent through the public
``POST /audio/transcriptions`` multipart surface.  Raw audio is never written to
disk or retained by this adapter after the request completes.
"""

from __future__ import annotations

import asyncio
import io
import json
import math
import re
import wave
from collections.abc import AsyncIterator, Awaitable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from eeveetuber.media import AsrFinal, AsrStreamEvent, PcmUtterance
from eeveetuber.runtime.cancellation import CancellationToken


@dataclass(frozen=True, slots=True)
class OpenAICompatibleAsrConfig:
    """Validated connection and request options for a transcription endpoint."""

    base_url: str = "https://api.openai.com/v1"
    api_key: str | None = field(default=None, repr=False)
    model: str | None = "whisper-1"
    language: str | None = None
    prompt: str | None = None
    temperature: float | None = None
    timeout_seconds: float = 30.0
    connect_timeout_seconds: float = 5.0
    max_input_pcm_bytes: int = 64 * 1024 * 1024
    max_response_bytes: int = 1 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.base_url or self.base_url.strip() != self.base_url:
            raise ValueError("base_url must be non-empty and trimmed")
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute HTTP(S) URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))

        for name, value in (
            ("api_key", self.api_key),
            ("model", self.model),
            ("language", self.language),
            ("prompt", self.prompt),
        ):
            if value is not None and (not value or value.strip() != value):
                raise ValueError(f"{name} must be non-empty and trimmed when provided")
        if self.temperature is not None and (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not math.isfinite(self.temperature)
            or not 0.0 <= self.temperature <= 1.0
        ):
            raise ValueError("temperature must be finite and between 0.0 and 1.0")
        self._require_positive_finite(self.timeout_seconds, "timeout_seconds")
        self._require_positive_finite(
            self.connect_timeout_seconds, "connect_timeout_seconds"
        )
        if (
            isinstance(self.max_input_pcm_bytes, bool)
            or not isinstance(self.max_input_pcm_bytes, int)
            or self.max_input_pcm_bytes < 2
        ):
            raise ValueError("max_input_pcm_bytes must be an integer of at least 2")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or self.max_response_bytes < 64
        ):
            raise ValueError("max_response_bytes must be an integer of at least 64")

    @staticmethod
    def _require_positive_finite(value: float, field_name: str) -> None:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0.0
        ):
            raise ValueError(f"{field_name} must be finite and greater than zero")

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/audio/transcriptions"):
            return self.base_url
        return f"{self.base_url}/audio/transcriptions"

    @property
    def timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.timeout_seconds, connect=self.connect_timeout_seconds)


class AsrAdapterError(RuntimeError):
    """Base for bounded, secret-safe transcription failures."""


class AsrAdapterClosed(AsrAdapterError):
    pass


class AsrTimeoutError(AsrAdapterError):
    pass


class AsrTransportError(AsrAdapterError):
    pass


class AsrProtocolError(AsrAdapterError):
    pass


class AsrHTTPError(AsrAdapterError):
    def __init__(self, status_code: int, *, request_id: str | None = None) -> None:
        self.status_code = status_code
        self.request_id = request_id
        suffix = f" (request_id={request_id})" if request_id else ""
        super().__init__(f"transcription provider returned HTTP {status_code}{suffix}")


class OpenAICompatibleSpeechRecognizer:
    """Return one normalized final transcript from one bounded PCM utterance."""

    def __init__(
        self,
        config: OpenAICompatibleAsrConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=config.timeout)
        self._closed = False
        self._state_lock = asyncio.Lock()
        self._active_responses: set[httpx.Response] = set()

    async def __aenter__(self) -> OpenAICompatibleSpeechRecognizer:
        self._ensure_open()
        return self

    async def __aexit__(
        self,
        _exc_type: object,
        _exc_value: object,
        _traceback: object,
    ) -> None:
        await self.aclose()

    async def recognize(
        self,
        utterance: PcmUtterance,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[AsrStreamEvent]:
        """Upload one in-memory WAV and yield exactly one terminal result."""

        self._ensure_open()
        if cancellation is not None:
            cancellation.raise_if_cancelled()
        if utterance.byte_count > self.config.max_input_pcm_bytes:
            raise AsrProtocolError("utterance PCM exceeded configured input byte limit")

        wav_audio = _encode_wav(utterance)
        request = self._client.build_request(
            "POST",
            self.config.endpoint,
            headers=self._headers(),
            data=self._form_fields(),
            files={"file": ("utterance.wav", wav_audio, "audio/wav")},
            timeout=self.config.timeout,
        )
        try:
            response = await _await_or_cancel(
                self._client.send(request, stream=True),
                cancellation,
            )
        except httpx.TimeoutException:
            raise AsrTimeoutError("transcription request timed out") from None
        except httpx.HTTPError as error:
            raise AsrTransportError(
                f"transcription transport failed ({type(error).__name__})"
            ) from None

        registered = False
        final: AsrFinal
        try:
            async with self._state_lock:
                if self._closed:
                    raise AsrAdapterClosed("transcription adapter is closed")
                self._active_responses.add(response)
                registered = True

            if not 200 <= response.status_code < 300:
                raise AsrHTTPError(
                    response.status_code,
                    request_id=_safe_request_id(response.headers.get("x-request-id")),
                )
            try:
                body = await _read_bounded_body(
                    response,
                    max_bytes=self.config.max_response_bytes,
                    cancellation=cancellation,
                )
            except httpx.TimeoutException:
                raise AsrTimeoutError("transcription response timed out") from None
            except httpx.HTTPError as error:
                if self._closed:
                    raise AsrAdapterClosed(
                        "transcription adapter closed during response"
                    ) from None
                raise AsrTransportError(
                    f"transcription response failed ({type(error).__name__})"
                ) from None
            final = self._normalize_response(utterance, body)
        finally:
            if registered:
                async with self._state_lock:
                    self._active_responses.discard(response)
            await response.aclose()

        if cancellation is not None:
            cancellation.raise_if_cancelled()
        yield final

    async def aclose(self) -> None:
        """Close active response streams and the internally owned client."""

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
            raise AsrAdapterClosed("transcription adapter is closed")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "eeveetuber/0.1",
        }
        if self.config.api_key is not None:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _form_fields(self) -> dict[str, str]:
        fields = {"response_format": "json"}
        if self.config.model is not None:
            fields["model"] = self.config.model
        if self.config.language is not None:
            fields["language"] = self.config.language
        if self.config.prompt is not None:
            fields["prompt"] = self.config.prompt
        if self.config.temperature is not None:
            fields["temperature"] = str(self.config.temperature)
        return fields

    def _normalize_response(self, utterance: PcmUtterance, body: bytes) -> AsrFinal:
        if not body:
            raise AsrProtocolError("transcription provider returned an empty response")
        try:
            payload: Any = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise AsrProtocolError("transcription provider returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise AsrProtocolError("transcription response must be a JSON object")
        if payload.get("error") is not None:
            raise AsrProtocolError("transcription provider returned an error payload")

        text = payload.get("text")
        if not isinstance(text, str):
            raise AsrProtocolError("transcription response omitted string text")

        language: str | None = self.config.language
        if "language" in payload:
            raw_language = payload["language"]
            if raw_language is not None and (
                not isinstance(raw_language, str) or not raw_language.strip()
            ):
                raise AsrProtocolError("transcription language must be a non-blank string")
            language = raw_language

        confidence: float | None = None
        if "confidence" in payload:
            raw_confidence = payload["confidence"]
            if (
                isinstance(raw_confidence, bool)
                or not isinstance(raw_confidence, (int, float))
                or not math.isfinite(raw_confidence)
                or not 0.0 <= raw_confidence <= 1.0
            ):
                raise AsrProtocolError(
                    "transcription confidence must be between 0.0 and 1.0"
                )
            confidence = float(raw_confidence)

        return AsrFinal(
            utterance_id=utterance.utterance_id,
            text=text,
            language=language,
            confidence=confidence,
        )


def _encode_wav(utterance: PcmUtterance) -> bytes:
    """Encode a standard PCM WAV into a transient in-memory buffer."""

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(utterance.format.channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(utterance.format.sample_rate_hz)
        wav_file.writeframes(utterance.pcm)
    return buffer.getvalue()


async def _read_bounded_body(
    response: httpx.Response,
    *,
    max_bytes: int,
    cancellation: CancellationToken | None,
) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None:
        try:
            declared_size = int(declared)
        except ValueError:
            declared_size = -1
        if declared_size > max_bytes:
            raise AsrProtocolError("transcription response exceeded configured byte limit")

    body = bytearray()
    iterator = response.aiter_bytes().__aiter__()
    while True:
        try:
            chunk = await _await_or_cancel(anext(iterator), cancellation)
        except StopAsyncIteration:
            break
        if len(body) + len(chunk) > max_bytes:
            raise AsrProtocolError("transcription response exceeded configured byte limit")
        body.extend(chunk)
    return bytes(body)


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


def _safe_request_id(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "", value)[:128]
    return cleaned or None


__all__ = [
    "AsrAdapterClosed",
    "AsrAdapterError",
    "AsrHTTPError",
    "AsrProtocolError",
    "AsrTimeoutError",
    "AsrTransportError",
    "OpenAICompatibleAsrConfig",
    "OpenAICompatibleSpeechRecognizer",
]
