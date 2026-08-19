"""OpenAI-compatible Chat Completions streaming without a vendor SDK.

The adapter targets the small, widely implemented ``/chat/completions`` SSE
surface.  A configurable base URL covers hosted providers and local servers such
as Ollama's OpenAI-compatible ``/v1`` endpoint without changing the core model
contract.
"""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Awaitable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import httpx

from eeveetuber.dialogue.types import (
    DialogueRequest,
    ModelCompleted,
    ModelStopReason,
    ModelStreamEvent,
    ModelTextDelta,
)
from eeveetuber.runtime.cancellation import CancellationToken


class ReasoningEffort(StrEnum):
    """Portable values accepted by reasoning-capable Chat Completions models."""

    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class OpenAICompatibleModelConfig:
    """Validated connection and generation settings for one model endpoint."""

    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False)
    timeout_seconds: float = 60.0
    connect_timeout_seconds: float = 10.0
    reasoning_effort: ReasoningEffort | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    include_usage: bool = True
    max_input_chars: int = 1_000_000
    max_error_body_bytes: int = 16_384
    max_sse_event_bytes: int = 262_144

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

        if not self.model or self.model.strip() != self.model:
            raise ValueError("model must be non-empty and trimmed")
        if self.api_key is not None and (
            not self.api_key or self.api_key.strip() != self.api_key
        ):
            raise ValueError("api_key must be non-empty and trimmed when provided")
        self._require_positive_finite(self.timeout_seconds, "timeout_seconds")
        self._require_positive_finite(
            self.connect_timeout_seconds, "connect_timeout_seconds"
        )
        if self.reasoning_effort is not None and not isinstance(
            self.reasoning_effort, ReasoningEffort
        ):
            raise TypeError("reasoning_effort must be a ReasoningEffort")
        if self.temperature is not None and (
            not math.isfinite(self.temperature) or not 0.0 <= self.temperature <= 2.0
        ):
            raise ValueError("temperature must be finite and between 0.0 and 2.0")
        if self.max_tokens is not None and self.max_tokens < 1:
            raise ValueError("max_tokens must be positive when provided")
        if self.max_input_chars < 1:
            raise ValueError("max_input_chars must be positive")
        if self.max_error_body_bytes < 256:
            raise ValueError("max_error_body_bytes must be at least 256")
        if self.max_sse_event_bytes < 1024:
            raise ValueError("max_sse_event_bytes must be at least 1024")

    @staticmethod
    def _require_positive_finite(value: float, field_name: str) -> None:
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError(f"{field_name} must be finite and greater than zero")

    @property
    def endpoint_url(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    @property
    def timeout(self) -> httpx.Timeout:
        return httpx.Timeout(self.timeout_seconds, connect=self.connect_timeout_seconds)


class OpenAICompatibleModelError(RuntimeError):
    """Base for sanitized, bounded errors safe to surface to runtime policy."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelTransportError(OpenAICompatibleModelError):
    """The HTTP exchange failed before a valid model stream completed."""


class ModelHTTPError(OpenAICompatibleModelError):
    """A non-success response from the configured endpoint."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        request_id: str | None,
    ) -> None:
        retryable = status_code in {408, 409, 425, 429} or status_code >= 500
        suffix = f" (request_id={request_id})" if request_id else ""
        super().__init__(
            f"model endpoint returned HTTP {status_code}: {detail}{suffix}",
            retryable=retryable,
        )
        self.status_code = status_code
        self.request_id = request_id


class ModelProtocolError(OpenAICompatibleModelError):
    """The endpoint returned an invalid or unsupported streaming shape."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


@dataclass(slots=True)
class _StreamState:
    stop_reason: ModelStopReason | None = None
    raw_stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class _SSEDecoder:
    """Incremental UTF-8 SSE data decoder with a strict per-event bound."""

    def __init__(self, max_event_bytes: int) -> None:
        self._max_event_bytes = max_event_bytes
        self._line_buffer = bytearray()
        self._data_lines: list[bytes] = []
        self._event_bytes = 0
        self._first_line = True

    def feed(self, chunk: bytes) -> tuple[str, ...]:
        self._line_buffer.extend(chunk)
        events: list[str] = []
        while True:
            newline = self._line_buffer.find(b"\n")
            if newline < 0:
                break
            raw_line = bytes(self._line_buffer[:newline])
            del self._line_buffer[: newline + 1]
            if raw_line.endswith(b"\r"):
                raw_line = raw_line[:-1]
            events.extend(self._consume_line(raw_line))
        if len(self._line_buffer) > self._max_event_bytes:
            raise ModelProtocolError("SSE line exceeded configured size limit")
        return tuple(events)

    def finish(self) -> tuple[str, ...]:
        events: list[str] = []
        if self._line_buffer:
            raw_line = bytes(self._line_buffer)
            self._line_buffer.clear()
            if raw_line.endswith(b"\r"):
                raw_line = raw_line[:-1]
            events.extend(self._consume_line(raw_line))
        events.extend(self._dispatch())
        return tuple(events)

    def _consume_line(self, raw_line: bytes) -> tuple[str, ...]:
        if self._first_line:
            self._first_line = False
            raw_line = raw_line.removeprefix(b"\xef\xbb\xbf")
        if len(raw_line) > self._max_event_bytes:
            raise ModelProtocolError("SSE line exceeded configured size limit")
        if not raw_line:
            return self._dispatch()
        if raw_line.startswith(b":"):
            return ()

        field_name, separator, value = raw_line.partition(b":")
        if not separator:
            value = b""
        elif value.startswith(b" "):
            value = value[1:]
        if field_name != b"data":
            return ()

        added_size = len(value) + (1 if self._data_lines else 0)
        if self._event_bytes + added_size > self._max_event_bytes:
            raise ModelProtocolError("SSE event exceeded configured size limit")
        self._data_lines.append(value)
        self._event_bytes += added_size
        return ()

    def _dispatch(self) -> tuple[str, ...]:
        if not self._data_lines:
            return ()
        payload = b"\n".join(self._data_lines)
        self._data_lines.clear()
        self._event_bytes = 0
        try:
            return (payload.decode("utf-8", errors="strict"),)
        except UnicodeDecodeError as error:
            raise ModelProtocolError("SSE data was not valid UTF-8") from error


class OpenAICompatibleModelProvider:
    """Stream normalized events from an OpenAI-compatible model endpoint."""

    def __init__(
        self,
        config: OpenAICompatibleModelConfig,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()
        self._closed = False

    @property
    def config(self) -> OpenAICompatibleModelConfig:
        return self._config

    async def __aenter__(self) -> OpenAICompatibleModelProvider:
        if self._closed:
            raise OpenAICompatibleModelError("model provider is closed", retryable=False)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    async def stream(
        self,
        request: DialogueRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AsyncIterator[ModelStreamEvent]:
        """Open one cancellable SSE request and yield one normalized completion."""

        if self._closed:
            raise OpenAICompatibleModelError("model provider is closed", retryable=False)
        if cancellation is not None:
            if cancellation.generation.value != request.generation:
                raise asyncio.CancelledError(
                    "cancellation token generation does not match dialogue request"
                )
            cancellation.raise_if_cancelled()
        payload = self._build_payload(request)
        headers = {
            "accept": "text/event-stream",
            "content-type": "application/json",
        }
        if self._config.api_key is not None:
            headers["authorization"] = f"Bearer {self._config.api_key}"

        stream_context = self._client.stream(
            "POST",
            self._config.endpoint_url,
            headers=headers,
            json=payload,
            timeout=self._config.timeout,
        )
        entered = False
        try:
            try:
                response = await _await_or_cancel(stream_context.__aenter__(), cancellation)
                entered = True
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                if response.status_code < 200 or response.status_code >= 300:
                    detail = await self._read_error_detail(response, cancellation)
                    raise ModelHTTPError(
                        response.status_code,
                        detail,
                        request_id=response.headers.get("x-request-id"),
                    )

                state = _StreamState()
                decoder = _SSEDecoder(self._config.max_sse_event_bytes)
                saw_done = False
                chunks = response.aiter_bytes().__aiter__()
                while True:
                    try:
                        chunk = await _await_or_cancel(anext(chunks), cancellation)
                    except StopAsyncIteration:
                        break
                    if cancellation is not None:
                        cancellation.raise_if_cancelled()
                    for data in decoder.feed(chunk):
                        if data.strip() == "[DONE]":
                            saw_done = True
                            break
                        if not data.strip():
                            continue
                        for text in self._normalize_data_event(data, state):
                            if cancellation is not None:
                                cancellation.raise_if_cancelled()
                            yield ModelTextDelta(text)
                    if saw_done:
                        break

                if not saw_done:
                    for data in decoder.finish():
                        if data.strip() == "[DONE]":
                            saw_done = True
                            continue
                        if not data.strip():
                            continue
                        for text in self._normalize_data_event(data, state):
                            if cancellation is not None:
                                cancellation.raise_if_cancelled()
                            yield ModelTextDelta(text)
                if not saw_done and state.stop_reason is None:
                    raise ModelProtocolError(
                        "model stream ended without [DONE] or a finish reason"
                    )

                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                yield ModelCompleted(
                    stop_reason=state.stop_reason or ModelStopReason.COMPLETE,
                    input_tokens=state.input_tokens,
                    output_tokens=state.output_tokens,
                )
            except OpenAICompatibleModelError:
                raise
            except httpx.TimeoutException as error:
                detail = self._bound_text(str(error) or "request timed out", 512)
                raise ModelTransportError(
                    f"model request timed out: {detail}", retryable=True
                ) from error
            except httpx.HTTPError as error:
                detail = self._bound_text(str(error) or type(error).__name__, 512)
                raise ModelTransportError(
                    f"model transport failed: {detail}", retryable=True
                ) from error
        finally:
            if entered:
                await stream_context.__aexit__(None, None, None)

    def _build_payload(self, request: DialogueRequest) -> dict[str, object]:
        input_chars = len(request.system_context) + len(request.user_text)
        if input_chars > self._config.max_input_chars:
            raise OpenAICompatibleModelError(
                "dialogue input exceeds configured character limit",
                retryable=False,
            )

        messages: list[dict[str, str]] = []
        if request.system_context.strip():
            messages.append({"role": "system", "content": request.system_context})
        messages.append({"role": "user", "content": request.user_text})
        payload: dict[str, object] = {
            "model": self._config.model,
            "messages": messages,
            "stream": True,
        }
        if self._config.include_usage:
            payload["stream_options"] = {"include_usage": True}
        if self._config.temperature is not None:
            payload["temperature"] = self._config.temperature
        if self._config.max_tokens is not None:
            payload["max_tokens"] = self._config.max_tokens
        if self._config.reasoning_effort is not None:
            payload["reasoning_effort"] = self._config.reasoning_effort.value
        return payload

    def _normalize_data_event(self, data: str, state: _StreamState) -> tuple[str, ...]:
        try:
            parsed: object = json.loads(data)
        except json.JSONDecodeError as error:
            excerpt = self._bound_text(data, 160)
            raise ModelProtocolError(f"invalid JSON in model SSE event: {excerpt}") from error
        if not isinstance(parsed, dict):
            raise ModelProtocolError("model SSE event must be a JSON object")

        error_payload = parsed.get("error")
        if error_payload is not None:
            detail = self._error_message_from_payload(error_payload)
            raise ModelProtocolError(f"model stream reported an error: {detail}")

        usage = parsed.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise ModelProtocolError("model usage must be an object")
            if "prompt_tokens" in usage:
                state.input_tokens = self._optional_token_count(usage["prompt_tokens"])
            if "completion_tokens" in usage:
                state.output_tokens = self._optional_token_count(
                    usage["completion_tokens"]
                )

        choices = parsed.get("choices")
        if choices is None:
            if usage is not None:
                return ()
            raise ModelProtocolError("model SSE event omitted choices")
        if not isinstance(choices, list):
            raise ModelProtocolError("model choices must be an array")
        if not choices:
            return ()

        choice = self._select_choice(choices)
        delta = choice.get("delta")
        text_parts: tuple[str, ...] = ()
        if delta is not None:
            if not isinstance(delta, dict):
                raise ModelProtocolError("choice delta must be an object")
            if delta.get("tool_calls") or delta.get("function_call"):
                raise ModelProtocolError(
                    "endpoint emitted tool calls on a text-only model contract"
                )
            text_parts = self._extract_text(delta.get("content"))

        raw_finish_reason = choice.get("finish_reason")
        if raw_finish_reason is not None:
            if not isinstance(raw_finish_reason, str) or not raw_finish_reason:
                raise ModelProtocolError("finish_reason must be a non-empty string or null")
            if (
                state.raw_stop_reason is not None
                and state.raw_stop_reason != raw_finish_reason
            ):
                raise ModelProtocolError("model stream emitted conflicting finish reasons")
            state.raw_stop_reason = raw_finish_reason
            state.stop_reason = self._map_stop_reason(raw_finish_reason)
        return text_parts

    @staticmethod
    def _select_choice(choices: list[object]) -> Mapping[str, Any]:
        candidates: list[Mapping[str, Any]] = []
        for item in choices:
            if not isinstance(item, dict):
                raise ModelProtocolError("each model choice must be an object")
            candidates.append(item)
        for candidate in candidates:
            if candidate.get("index") == 0:
                return candidate
        return candidates[0]

    @staticmethod
    def _extract_text(content: object) -> tuple[str, ...]:
        if content is None:
            return ()
        if isinstance(content, str):
            return (content,) if content else ()
        if not isinstance(content, list):
            raise ModelProtocolError("delta content must be text, text parts, or null")

        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                raise ModelProtocolError("delta content parts must be objects")
            part_type = part.get("type")
            text = part.get("text")
            if part_type not in {"text", "output_text"} or not isinstance(text, str):
                raise ModelProtocolError("unsupported non-text delta content part")
            if text:
                parts.append(text)
        return tuple(parts)

    @staticmethod
    def _map_stop_reason(reason: str) -> ModelStopReason:
        normalized = reason.strip().lower()
        if normalized in {"stop", "complete", "completed", "eos", "end_turn"}:
            return ModelStopReason.COMPLETE
        if normalized in {"length", "max_tokens", "max_output_tokens"}:
            return ModelStopReason.LENGTH
        if normalized in {"cancelled", "canceled"}:
            return ModelStopReason.CANCELLED
        return ModelStopReason.ERROR

    @staticmethod
    def _optional_token_count(value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ModelProtocolError("token usage values must be non-negative integers")
        return value

    async def _read_error_detail(
        self,
        response: httpx.Response,
        cancellation: CancellationToken | None,
    ) -> str:
        limit = self._config.max_error_body_bytes
        body = bytearray()
        truncated = False
        chunks = response.aiter_bytes().__aiter__()
        while True:
            try:
                chunk = await _await_or_cancel(anext(chunks), cancellation)
            except StopAsyncIteration:
                break
            remaining = limit - len(body)
            if remaining <= 0:
                truncated = True
                break
            body.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True
                break

        decoded = bytes(body).decode("utf-8", errors="replace")
        detail = decoded
        try:
            payload: object = json.loads(decoded)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and "error" in payload:
            detail = self._error_message_from_payload(payload["error"])
        detail = self._bound_text(detail, limit)
        if truncated:
            detail = f"{detail} [truncated]"
        return detail or "empty error response"

    def _error_message_from_payload(self, payload: object) -> str:
        if isinstance(payload, dict):
            message = payload.get("message")
            error_type = payload.get("type")
            if isinstance(message, str):
                if isinstance(error_type, str) and error_type:
                    return self._bound_text(f"{error_type}: {message}", 1024)
                return self._bound_text(message, 1024)
        if isinstance(payload, str):
            return self._bound_text(payload, 1024)
        return "unspecified provider error"

    @staticmethod
    def _bound_text(text: str, limit: int) -> str:
        collapsed = " ".join(text.split())
        if len(collapsed) <= limit:
            return collapsed
        return f"{collapsed[:limit]}…"


async def _await_or_cancel[ResultT](
    awaitable: Awaitable[ResultT],
    cancellation: CancellationToken | None,
) -> ResultT:
    """Race one HTTP wait against session cancellation without leaking tasks."""

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
        if operation in done:
            return operation.result()
        reason = cancelled.result()
        operation.cancel()
        with suppress(asyncio.CancelledError):
            await operation
        raise asyncio.CancelledError(reason)
    finally:
        if not operation.done():
            operation.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await operation
        cancelled.cancel()
        with suppress(asyncio.CancelledError):
            await cancelled


__all__ = [
    "ModelHTTPError",
    "ModelProtocolError",
    "ModelTransportError",
    "OpenAICompatibleModelConfig",
    "OpenAICompatibleModelError",
    "OpenAICompatibleModelProvider",
    "ReasoningEffort",
]
