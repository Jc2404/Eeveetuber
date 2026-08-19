import asyncio
import json
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest

from eeveetuber.adapters.openai_compatible import (
    ModelHTTPError,
    ModelProtocolError,
    ModelTransportError,
    OpenAICompatibleModelConfig,
    OpenAICompatibleModelError,
    OpenAICompatibleModelProvider,
    ReasoningEffort,
)
from eeveetuber.dialogue.types import (
    DialogueRequest,
    ModelCompleted,
    ModelStopReason,
    ModelTextDelta,
)
from eeveetuber.runtime.cancellation import CancellationSource


def _request(
    *,
    generation: int = 0,
    user_text: str = "Hello",
    system_context: str = "You are concise.",
) -> DialogueRequest:
    return DialogueRequest(
        session_id=uuid4(),
        turn_id=uuid4(),
        generation=generation,
        user_text=user_text,
        system_context=system_context,
        metadata={"model": "must-not-override", "api_key": "must-not-leak"},
    )


def _sse(*events: object, done: bool = True) -> bytes:
    blocks = [f"data: {json.dumps(event)}\n\n" for event in events]
    if done:
        blocks.append("data: [DONE]\n\n")
    return "".join(blocks).encode()


async def _collect(
    provider: OpenAICompatibleModelProvider,
    request: DialogueRequest | None = None,
) -> list[ModelTextDelta | ModelCompleted]:
    return [event async for event in provider.stream(request or _request())]


@pytest.mark.asyncio
async def test_streams_text_and_completion_with_usage_and_safe_payload() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["payload"] = json.loads(request.content)
        body = b": keepalive\n\n" + _sse(
            {"choices": [{"index": 0, "delta": {"role": "assistant"}}]},
            {"choices": [{"index": 0, "delta": {"content": "Hello "}}]},
            {"choices": [{"index": 0, "delta": {"content": "there"}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            {
                "choices": [],
                "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
            },
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)

    config = OpenAICompatibleModelConfig(
        base_url="https://models.example/v1/",
        model="reasoning-model",
        api_key="secret-key",
        reasoning_effort=ReasoningEffort.LOW,
        temperature=0.4,
        max_tokens=321,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(config, client=client)
        events = await _collect(provider)

    assert [event.text for event in events if isinstance(event, ModelTextDelta)] == [
        "Hello ",
        "there",
    ]
    completion = events[-1]
    assert isinstance(completion, ModelCompleted)
    assert completion == ModelCompleted(
        stop_reason=ModelStopReason.COMPLETE,
        input_tokens=11,
        output_tokens=2,
    )
    assert captured["url"] == "https://models.example/v1/chat/completions"
    headers = captured["headers"]
    assert isinstance(headers, httpx.Headers)
    assert headers["authorization"] == "Bearer secret-key"
    assert headers["accept"] == "text/event-stream"
    assert captured["payload"] == {
        "model": "reasoning-model",
        "messages": [
            {"role": "system", "content": "You are concise."},
            {"role": "user", "content": "Hello"},
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": 0.4,
        "max_tokens": 321,
        "reasoning_effort": "low",
    }
    assert "must-not-override" not in json.dumps(captured["payload"])
    assert "must-not-leak" not in json.dumps(captured["payload"])
    assert "secret-key" not in repr(config)


@pytest.mark.asyncio
async def test_local_endpoint_needs_no_api_key_or_separate_adapter() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            content=_sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        )

    config = OpenAICompatibleModelConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen3:8b",
        include_usage=False,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = await _collect(OpenAICompatibleModelProvider(config, client=client))

    assert isinstance(events[-1], ModelCompleted)
    assert captured["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    assert captured["authorization"] is None
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert "stream_options" not in payload
    assert "reasoning_effort" not in payload


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", list(ReasoningEffort))
async def test_reasoning_effort_values_are_sent_without_collapsing_none(
    effort: ReasoningEffort,
) -> None:
    captured_payload: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_payload.update(json.loads(request.content))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    config = OpenAICompatibleModelConfig(
        base_url="http://127.0.0.1:11434/v1",
        model="local-model",
        reasoning_effort=effort,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await _collect(OpenAICompatibleModelProvider(config, client=client))

    assert captured_payload["reasoning_effort"] == effort.value


@pytest.mark.asyncio
async def test_ollama_style_empty_completion_preserves_finish_and_zero_usage() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                {
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "length"}],
                    "usage": {
                        "prompt_tokens": 27,
                        "completion_tokens": 0,
                        "total_tokens": 27,
                    },
                }
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = await _collect(
            OpenAICompatibleModelProvider(
                OpenAICompatibleModelConfig(
                    "http://127.0.0.1:11434/v1",
                    "local-model",
                    reasoning_effort=ReasoningEffort.NONE,
                ),
                client=client,
            )
        )

    assert events == [
        ModelCompleted(
            stop_reason=ModelStopReason.LENGTH,
            input_tokens=27,
            output_tokens=0,
        )
    ]


@pytest.mark.asyncio
async def test_full_chat_completions_url_is_not_duplicated() -> None:
    seen_url = ""

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_url
        seen_url = str(request.url)
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    config = OpenAICompatibleModelConfig(
        base_url="https://proxy.example/custom/chat/completions",
        model="model",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await _collect(OpenAICompatibleModelProvider(config, client=client))

    assert seen_url == "https://proxy.example/custom/chat/completions"


@pytest.mark.asyncio
async def test_blank_system_context_is_omitted_not_rewritten() -> None:
    payload: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload.update(json.loads(request.content))
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            OpenAICompatibleModelConfig("http://localhost:11434/v1", "local"),
            client=client,
        )
        await _collect(provider, _request(system_context="   "))

    assert payload["messages"] == [{"role": "user", "content": "Hello"}]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_url": "localhost:11434/v1", "model": "m"},
        {"base_url": "ftp://example/v1", "model": "m"},
        {"base_url": "https://user:pass@example/v1", "model": "m"},
        {"base_url": "https://example/v1?secret=yes", "model": "m"},
        {"base_url": "https://example/v1", "model": " "},
        {"base_url": "https://example/v1", "model": "m", "api_key": " "},
        {"base_url": "https://example/v1", "model": "m", "timeout_seconds": 0},
        {"base_url": "https://example/v1", "model": "m", "temperature": 2.1},
        {"base_url": "https://example/v1", "model": "m", "max_tokens": 0},
        {"base_url": "https://example/v1", "model": "m", "max_sse_event_bytes": 12},
    ],
)
def test_config_rejects_unsafe_or_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        OpenAICompatibleModelConfig(**kwargs)  # type: ignore[arg-type]


def test_config_rejects_untyped_reasoning_effort() -> None:
    with pytest.raises(TypeError, match="ReasoningEffort"):
        OpenAICompatibleModelConfig(  # type: ignore[arg-type]
            "https://example/v1", "m", reasoning_effort="high"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_reason", "normalized"),
    [
        ("length", ModelStopReason.LENGTH),
        ("max_tokens", ModelStopReason.LENGTH),
        ("cancelled", ModelStopReason.CANCELLED),
        ("content_filter", ModelStopReason.ERROR),
        ("end_turn", ModelStopReason.COMPLETE),
    ],
)
async def test_normalizes_finish_reasons(
    provider_reason: str,
    normalized: ModelStopReason,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {}, "finish_reason": provider_reason}]}
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = await _collect(
            OpenAICompatibleModelProvider(
                OpenAICompatibleModelConfig("https://example/v1", "m"),
                client=client,
            )
        )

    assert events[-1] == ModelCompleted(stop_reason=normalized)


@pytest.mark.asyncio
async def test_accepts_finish_reason_without_done_for_compatible_local_servers() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
                done=False,
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = await _collect(
            OpenAICompatibleModelProvider(
                OpenAICompatibleModelConfig("http://localhost:8000/v1", "m"),
                client=client,
            )
        )

    assert events == [ModelTextDelta("ok"), ModelCompleted()]


@pytest.mark.asyncio
async def test_supports_text_part_deltas_but_rejects_non_text_parts() -> None:
    responses = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "content": [
                                {"type": "text", "text": "one"},
                                {"type": "output_text", "text": " two"},
                            ]
                        },
                        "finish_reason": "stop",
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {"delta": {"content": [{"type": "image", "url": "x"}]}}
                ]
            }
        ),
    ]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=responses.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            OpenAICompatibleModelConfig("https://example/v1", "m"),
            client=client,
        )
        first = await _collect(provider)
        assert first[:2] == [ModelTextDelta("one"), ModelTextDelta(" two")]
        with pytest.raises(ModelProtocolError, match="non-text"):
            await _collect(provider)


@pytest.mark.asyncio
async def test_never_exposes_reasoning_or_tool_calls_as_text() -> None:
    responses = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {"reasoning_content": "private", "content": "public"},
                        "finish_reason": "stop",
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {"delta": {"tool_calls": [{"function": {"name": "danger"}}]}}
                ]
            }
        ),
    ]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=responses.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            OpenAICompatibleModelConfig("https://example/v1", "m"),
            client=client,
        )
        safe = await _collect(provider)
        assert safe[0] == ModelTextDelta("public")
        with pytest.raises(ModelProtocolError, match="tool calls"):
            await _collect(provider)


@pytest.mark.asyncio
async def test_rejects_truncated_stream_malformed_json_and_bad_usage() -> None:
    responses = [
        _sse({"choices": [{"delta": {"content": "partial"}}]}, done=False),
        b"data: {not-json}\n\n",
        _sse({"choices": [], "usage": {"prompt_tokens": True}}),
    ]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=responses.pop(0))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            OpenAICompatibleModelConfig("https://example/v1", "m"),
            client=client,
        )
        with pytest.raises(ModelProtocolError, match=r"without \[DONE\]"):
            await _collect(provider)
        with pytest.raises(ModelProtocolError, match="invalid JSON"):
            await _collect(provider)
        with pytest.raises(ModelProtocolError, match="token usage"):
            await _collect(provider)


@pytest.mark.asyncio
async def test_http_error_is_typed_retryable_and_bounded() -> None:
    huge = "failure " * 2_000

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"x-request-id": "req-123"},
            content=huge.encode(),
        )

    config = OpenAICompatibleModelConfig(
        "https://example/v1",
        "m",
        max_error_body_bytes=256,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(config, client=client)
        with pytest.raises(ModelHTTPError) as caught:
            await _collect(provider)

    assert caught.value.status_code == 429
    assert caught.value.request_id == "req-123"
    assert caught.value.retryable
    assert "[truncated]" in str(caught.value)
    assert len(str(caught.value)) < 400


@pytest.mark.asyncio
async def test_structured_http_error_extracts_only_bounded_message() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"type": "invalid_request", "message": "bad model"}},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            OpenAICompatibleModelConfig("https://example/v1", "m"),
            client=client,
        )
        with pytest.raises(ModelHTTPError) as caught:
            await _collect(provider)

    assert "invalid_request: bad model" in str(caught.value)
    assert not caught.value.retryable


@pytest.mark.asyncio
async def test_stream_error_and_oversized_sse_event_are_bounded_protocol_errors() -> None:
    responses = [
        _sse({"error": {"type": "server_error", "message": "failed"}}),
        b"data: " + b"x" * 1_025 + b"\n\n",
    ]

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=responses.pop(0))

    config = OpenAICompatibleModelConfig(
        "https://example/v1", "m", max_sse_event_bytes=1024
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(config, client=client)
        with pytest.raises(ModelProtocolError, match="server_error: failed"):
            await _collect(provider)
        with pytest.raises(ModelProtocolError, match="size limit"):
            await _collect(provider)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
async def test_transport_failures_are_sanitized_and_retryable(error_type) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("network unavailable", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            OpenAICompatibleModelConfig("https://example/v1", "m"),
            client=client,
        )
        with pytest.raises(ModelTransportError) as caught:
            await _collect(provider)

    assert caught.value.retryable
    assert "network unavailable" in str(caught.value)


@pytest.mark.asyncio
async def test_input_limit_is_checked_before_network_io() -> None:
    called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    config = OpenAICompatibleModelConfig(
        "https://example/v1", "m", max_input_chars=5
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(config, client=client)
        with pytest.raises(OpenAICompatibleModelError, match="character limit"):
            await _collect(provider, _request(user_text="12345", system_context="x"))
    assert not called


class _BlockingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield _sse({"choices": [{"delta": {"content": "first"}}]}, done=False)
        self.waiting.set()
        await self.release.wait()
        yield _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]})

    async def aclose(self) -> None:
        self.closed = True
        self.release.set()


@pytest.mark.asyncio
async def test_async_generator_aclose_closes_streaming_response() -> None:
    body = _BlockingStream()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            OpenAICompatibleModelConfig("https://example/v1", "m"),
            client=client,
        )
        stream = provider.stream(_request())
        assert await anext(stream) == ModelTextDelta("first")
        await stream.aclose()

    assert body.closed


@pytest.mark.asyncio
async def test_cancellation_token_aborts_blocked_read_and_closes_response() -> None:
    body = _BlockingStream()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body)

    source = CancellationSource(initial_generation=7)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            OpenAICompatibleModelConfig("https://example/v1", "m"),
            client=client,
        )
        stream = provider.stream(
            _request(generation=7),
            cancellation=source.token(),
        )
        assert await anext(stream) == ModelTextDelta("first")
        blocked_read = asyncio.create_task(anext(stream))
        await asyncio.wait_for(body.waiting.wait(), timeout=1.0)
        source.cancel_current("barge-in")
        with pytest.raises(asyncio.CancelledError, match="barge-in"):
            await asyncio.wait_for(blocked_read, timeout=1.0)

    assert body.closed


@pytest.mark.asyncio
async def test_task_cancellation_aborts_blocked_read_without_leaking_response() -> None:
    body = _BlockingStream()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            OpenAICompatibleModelConfig("https://example/v1", "m"),
            client=client,
        )
        stream = provider.stream(_request())
        assert await anext(stream) == ModelTextDelta("first")
        blocked_read = asyncio.create_task(anext(stream))
        await asyncio.wait_for(body.waiting.wait(), timeout=1.0)
        blocked_read.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocked_read

    assert body.closed


@pytest.mark.asyncio
async def test_pre_cancelled_or_mismatched_generation_never_opens_request() -> None:
    called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"data: [DONE]\n\n")

    source = CancellationSource(initial_generation=2)
    old = source.token()
    source.cancel_current("stopped")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleModelProvider(
            OpenAICompatibleModelConfig("https://example/v1", "m"),
            client=client,
        )
        with pytest.raises(asyncio.CancelledError):
            await anext(provider.stream(_request(generation=2), cancellation=old))
        with pytest.raises(asyncio.CancelledError, match="does not match"):
            await anext(
                provider.stream(
                    _request(generation=3),
                    cancellation=source.token(),
                )
            )

    assert not called


@pytest.mark.asyncio
async def test_provider_close_is_idempotent_and_does_not_own_injected_client() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as client:
        provider = OpenAICompatibleModelProvider(
            OpenAICompatibleModelConfig("https://example/v1", "m"),
            client=client,
        )
        await provider.aclose()
        await provider.aclose()
        assert not client.is_closed
        with pytest.raises(OpenAICompatibleModelError, match="closed"):
            await anext(provider.stream(_request()))
