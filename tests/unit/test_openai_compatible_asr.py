from __future__ import annotations

import asyncio
import io
import json
import struct
import wave
from collections.abc import AsyncIterator
from email import policy
from email.message import Message
from email.parser import BytesParser
from uuid import UUID

import httpx
import pytest

from eeveetuber.adapters.openai_compatible import (
    AsrAdapterClosed,
    AsrHTTPError,
    AsrProtocolError,
    AsrTimeoutError,
    AsrTransportError,
    OpenAICompatibleAsrConfig,
    OpenAICompatibleSpeechRecognizer,
)
from eeveetuber.media import AsrFinal, PcmFormat, PcmFrame, PcmUtterance, UtteranceEndReason
from eeveetuber.runtime import CancellationSource

STREAM_ID = UUID("00000000-0000-0000-0000-000000000040")
UTTERANCE_ID = UUID("00000000-0000-0000-0000-000000000041")


def _utterance(*, channels: int = 1) -> PcmUtterance:
    pcm_format = PcmFormat(16_000, channels=channels)
    sample_count = 160 * channels
    frame = PcmFrame(
        stream_id=STREAM_ID,
        sequence=0,
        captured_at_monotonic_ns=1_000,
        format=pcm_format,
        pcm=struct.pack(f"<{sample_count}h", *([250] * sample_count)),
    )
    return PcmUtterance(
        utterance_id=UTTERANCE_ID,
        stream_id=STREAM_ID,
        format=pcm_format,
        frames=(frame,),
        speech_started_at_monotonic_ns=1_000,
        speech_ended_at_monotonic_ns=frame.end_monotonic_ns,
        end_reason=UtteranceEndReason.STREAM_ENDED,
    )


async def _collect(
    recognizer: OpenAICompatibleSpeechRecognizer,
    utterance: PcmUtterance | None = None,
    *,
    cancellation=None,
):
    return [
        event
        async for event in recognizer.recognize(
            utterance or _utterance(), cancellation=cancellation
        )
    ]


def _multipart_parts(request: httpx.Request, body: bytes) -> dict[str, Message]:
    envelope = (
        f"Content-Type: {request.headers['content-type']}\r\n"
        "MIME-Version: 1.0\r\n\r\n"
    ).encode() + body
    message = BytesParser(policy=policy.default).parsebytes(envelope)
    parts: dict[str, Message] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        assert isinstance(name, str)
        parts[name] = part
    return parts


@pytest.mark.asyncio
async def test_posts_valid_in_memory_wav_and_normalizes_one_final() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        body = await request.aread()
        captured.update(
            url=str(request.url),
            authorization=request.headers.get("authorization"),
            parts=_multipart_parts(request, body),
        )
        return httpx.Response(
            200,
            json={"text": "hello Eevee", "language": "en", "confidence": 0.875},
        )

    config = OpenAICompatibleAsrConfig(
        base_url="http://127.0.0.1:8000/v1",
        api_key=None,
        model="whisper-local",
        language="en",
        prompt="VTuber names",
        temperature=0.25,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = await _collect(OpenAICompatibleSpeechRecognizer(config, client=client))

    assert events == [AsrFinal(UTTERANCE_ID, "hello Eevee", "en", 0.875)]
    assert captured["url"] == "http://127.0.0.1:8000/v1/audio/transcriptions"
    assert captured["authorization"] is None
    parts = captured["parts"]
    assert isinstance(parts, dict)
    assert parts["model"].get_payload(decode=True) == b"whisper-local"
    assert parts["language"].get_payload(decode=True) == b"en"
    assert parts["prompt"].get_payload(decode=True) == b"VTuber names"
    assert parts["temperature"].get_payload(decode=True) == b"0.25"
    assert parts["response_format"].get_payload(decode=True) == b"json"

    file_part = parts["file"]
    assert file_part.get_filename() == "utterance.wav"
    wav_bytes = file_part.get_payload(decode=True)
    assert isinstance(wav_bytes, bytes)
    with wave.open(io.BytesIO(wav_bytes), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16_000
        assert wav_file.readframes(wav_file.getnframes()) == _utterance().pcm


@pytest.mark.asyncio
async def test_keyless_endpoint_can_omit_model_and_accept_full_endpoint_url() -> None:
    captured_parts: dict[str, Message] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://localhost:9000/audio/transcriptions"
        captured_parts.update(_multipart_parts(request, await request.aread()))
        return httpx.Response(200, json={"text": "local"})

    config = OpenAICompatibleAsrConfig(
        "http://localhost:9000/audio/transcriptions",
        model=None,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        events = await _collect(OpenAICompatibleSpeechRecognizer(config, client=client))

    assert events == [AsrFinal(UTTERANCE_ID, "local")]
    assert "model" not in captured_parts
    assert "authorization" not in captured_parts


@pytest.mark.asyncio
async def test_api_key_is_sent_but_never_exposed_by_http_error() -> None:
    secret = "sk-do-not-leak"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {secret}"
        return httpx.Response(
            401,
            headers={"x-request-id": "req-123\r\nsecret"},
            content=f"invalid key {secret}".encode(),
        )

    config = OpenAICompatibleAsrConfig("https://example.test/v1", api_key=secret)
    assert secret not in repr(config)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        recognizer = OpenAICompatibleSpeechRecognizer(config, client=client)
        with pytest.raises(AsrHTTPError) as caught:
            await _collect(recognizer)

    assert caught.value.status_code == 401
    assert caught.value.request_id == "req-123secret"
    assert secret not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"text": 123}),
        httpx.Response(200, json={"error": {"message": "secret detail"}}),
        httpx.Response(200, json={"text": "ok", "confidence": True}),
    ],
)
async def test_rejects_invalid_response_shapes_without_echoing_body(
    response: httpx.Response,
) -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        recognizer = OpenAICompatibleSpeechRecognizer(
            OpenAICompatibleAsrConfig("https://example.test/v1"), client=client
        )
        with pytest.raises(AsrProtocolError) as caught:
            await _collect(recognizer)

    assert "secret detail" not in str(caught.value)


class _TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_response_size_limit_is_incremental_and_closes_stream() -> None:
    body = _TrackingStream((b"x" * 40, b"y" * 40))

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body)

    config = OpenAICompatibleAsrConfig(
        "https://example.test/v1", max_response_bytes=64
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(AsrProtocolError, match="byte limit"):
            await _collect(OpenAICompatibleSpeechRecognizer(config, client=client))

    assert body.closed


class _BlockingStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.waiting.set()
        await self.release.wait()
        yield json.dumps({"text": "too late"}).encode()

    async def aclose(self) -> None:
        self.closed = True
        self.release.set()


@pytest.mark.asyncio
async def test_cancellation_aborts_blocked_response_and_closes_it() -> None:
    body = _BlockingStream()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=body)

    source = CancellationSource()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        recognizer = OpenAICompatibleSpeechRecognizer(
            OpenAICompatibleAsrConfig("https://example.test/v1"), client=client
        )
        pending = asyncio.create_task(_collect(recognizer, cancellation=source.token()))
        await asyncio.wait_for(body.waiting.wait(), timeout=1.0)
        source.cancel_current("barge-in")
        with pytest.raises(asyncio.CancelledError, match="barge-in"):
            await asyncio.wait_for(pending, timeout=1.0)

    assert body.closed


@pytest.mark.asyncio
async def test_pre_cancelled_token_and_oversize_input_never_open_request() -> None:
    called = False

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"text": "unexpected"})

    source = CancellationSource()
    source.cancel_current("already stopped")
    config = OpenAICompatibleAsrConfig(
        "https://example.test/v1", max_input_pcm_bytes=100
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        recognizer = OpenAICompatibleSpeechRecognizer(config, client=client)
        with pytest.raises(asyncio.CancelledError, match="already stopped"):
            await _collect(recognizer, cancellation=source.token())
        with pytest.raises(AsrProtocolError, match="input byte limit"):
            await _collect(recognizer)

    assert not called


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [httpx.ReadTimeout, httpx.ConnectError])
async def test_transport_failures_are_typed_and_secret_safe(error_type) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise error_type("provider echoed sk-secret", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        recognizer = OpenAICompatibleSpeechRecognizer(
            OpenAICompatibleAsrConfig("https://example.test/v1", api_key="sk-secret"),
            client=client,
        )
        expected = AsrTimeoutError if error_type is httpx.ReadTimeout else AsrTransportError
        with pytest.raises(expected) as caught:
            await _collect(recognizer)

    assert "sk-secret" not in str(caught.value)


@pytest.mark.asyncio
async def test_close_is_idempotent_and_does_not_own_injected_client() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200))
    ) as client:
        recognizer = OpenAICompatibleSpeechRecognizer(
            OpenAICompatibleAsrConfig("https://example.test/v1"), client=client
        )
        await recognizer.aclose()
        await recognizer.aclose()
        assert not client.is_closed
        with pytest.raises(AsrAdapterClosed):
            await _collect(recognizer)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_url": "localhost:8000/v1"},
        {"base_url": "ftp://example.test/v1"},
        {"base_url": "https://user:pass@example.test/v1"},
        {"base_url": "https://example.test/v1?key=secret"},
        {"base_url": "https://example.test/v1", "api_key": " "},
        {"base_url": "https://example.test/v1", "model": " "},
        {"base_url": "https://example.test/v1", "language": " en"},
        {"base_url": "https://example.test/v1", "prompt": ""},
        {"base_url": "https://example.test/v1", "temperature": 1.1},
        {"base_url": "https://example.test/v1", "timeout_seconds": 0},
        {"base_url": "https://example.test/v1", "max_response_bytes": 63},
    ],
)
def test_config_rejects_invalid_or_unsafe_values(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        OpenAICompatibleAsrConfig(**kwargs)  # type: ignore[arg-type]
