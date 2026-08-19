from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

import httpx
import pytest

from eeveetuber.adapters.openai_compatible import (
    OpenAICompatibleSpeechConfig,
    OpenAICompatibleSpeechSynthesizer,
    SpeechAdapterClosed,
    SpeechAudioFormat,
    SpeechHTTPError,
    SpeechProtocolError,
    SpeechTransportError,
)
from eeveetuber.dialogue.ports import SpeechSynthesizer
from eeveetuber.dialogue.types import UtteranceSegment
from eeveetuber.runtime import CancellationSource

SEGMENT = UtteranceSegment(
    sequence=3,
    segment_id=UUID("12345678-1234-5678-1234-567812345678"),
    speakable_text="Hello from Eevee.",
    display_text="Hello from Eevee.",
    affect="cheerful",
    delivery="quick and warm",
)


async def collect(synthesizer: OpenAICompatibleSpeechSynthesizer) -> list[object]:
    return [chunk async for chunk in synthesizer.synthesize(SEGMENT)]


def test_adapter_satisfies_speech_port() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"audio")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter: SpeechSynthesizer = OpenAICompatibleSpeechSynthesizer(
        OpenAICompatibleSpeechConfig(base_url="http://localhost:8000/v1"),
        client=client,
    )
    assert adapter is not None


@pytest.mark.asyncio
async def test_streams_bounded_ordered_chunks_with_deterministic_metadata() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "audio/pcm"},
            content=b"abcdefghij",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleSpeechSynthesizer(
        OpenAICompatibleSpeechConfig(
            base_url="http://127.0.0.1:8080/v1/",
            model="local-tts",
            voice="eevee",
            response_format=SpeechAudioFormat.PCM,
            sample_rate_hz=16_000,
            chunk_size_bytes=4,
            send_sample_rate=True,
            use_segment_instructions=True,
        ),
        client=client,
    )

    chunks = [chunk async for chunk in adapter.synthesize(SEGMENT)]

    assert captured == {
        "url": "http://127.0.0.1:8080/v1/audio/speech",
        "authorization": None,
        "body": {
            "model": "local-tts",
            "input": "Hello from Eevee.",
            "voice": "eevee",
            "response_format": "pcm",
            "sample_rate": 16_000,
            "instructions": "Affect: cheerful. Delivery: quick and warm",
        },
    }
    assert b"".join(chunk.audio for chunk in chunks) == b"abcdefghij"
    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]
    assert [chunk.is_final for chunk in chunks] == [False, False, True]
    assert all(len(chunk.audio) <= 4 for chunk in chunks)
    assert all(chunk.segment_id == SEGMENT.segment_id for chunk in chunks)
    assert all(chunk.sequence == SEGMENT.sequence for chunk in chunks)
    assert all(chunk.media_type == "audio/pcm" for chunk in chunks)
    assert all(chunk.sample_rate_hz == 16_000 for chunk in chunks)
    assert all(chunk.duration_ms is not None for chunk in chunks)
    await adapter.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_optional_api_key_is_sent_but_never_in_config_repr() -> None:
    secret = "sk-local-secret"
    authorization: str | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal authorization
        authorization = request.headers.get("authorization")
        return httpx.Response(200, content=b"mp3")

    config = OpenAICompatibleSpeechConfig(
        base_url="https://speech.example/v1",
        api_key=secret,
        response_format=SpeechAudioFormat.MP3,
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleSpeechSynthesizer(config, client=client)

    chunks = [chunk async for chunk in adapter.synthesize(SEGMENT)]

    assert authorization == f"Bearer {secret}"
    assert secret not in repr(config)
    assert chunks[0].media_type == "audio/mpeg"
    assert chunks[0].duration_ms is None
    await adapter.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_http_error_does_not_expose_body_or_credentials() -> None:
    secret = "sk-do-not-leak"

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            headers={"x-request-id": "req-safe\r\ninjected"},
            json={"error": f"credential {secret} was rejected"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleSpeechSynthesizer(
        OpenAICompatibleSpeechConfig(api_key=secret),
        client=client,
    )

    with pytest.raises(SpeechHTTPError) as captured:
        await collect(adapter)

    assert captured.value.status_code == 401
    assert secret not in str(captured.value)
    assert "credential" not in str(captured.value)
    assert "\r" not in str(captured.value)
    assert "\n" not in str(captured.value)
    await adapter.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_transport_failure_is_normalized_without_original_message() -> None:
    secret = "transport-secret"

    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed with {secret}", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleSpeechSynthesizer(
        OpenAICompatibleSpeechConfig(),
        client=client,
    )

    with pytest.raises(SpeechTransportError) as captured:
        await collect(adapter)

    assert secret not in str(captured.value)
    assert "ConnectError" in str(captured.value)
    await adapter.aclose()
    await client.aclose()


class GatedAudioStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.release_last = asyncio.Event()
        self.closed = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"aaaa"
        yield b"bbbb"
        await self.release_last.wait()
        yield b"cccc"

    async def aclose(self) -> None:
        self.closed.set()


@pytest.mark.asyncio
async def test_cancellation_closes_in_flight_response_promptly() -> None:
    stream = GatedAudioStream()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleSpeechSynthesizer(
        OpenAICompatibleSpeechConfig(chunk_size_bytes=4),
        client=client,
    )
    cancellation = CancellationSource()
    generator = adapter.synthesize(SEGMENT, cancellation=cancellation.token())

    first = await anext(generator)
    assert first.audio == b"aaaa"
    assert first.is_final is False
    waiting = asyncio.create_task(anext(generator))
    await asyncio.sleep(0)
    cancellation.cancel_current("user interrupted")

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiting, timeout=0.25)
    await asyncio.wait_for(stream.closed.wait(), timeout=0.25)
    await adapter.aclose()
    await client.aclose()


@pytest.mark.asyncio
async def test_generator_close_releases_response_and_empty_audio_is_rejected() -> None:
    stream = GatedAudioStream()

    async def streaming_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    client = httpx.AsyncClient(transport=httpx.MockTransport(streaming_handler))
    adapter = OpenAICompatibleSpeechSynthesizer(
        OpenAICompatibleSpeechConfig(chunk_size_bytes=4),
        client=client,
    )
    generator = adapter.synthesize(SEGMENT)
    await anext(generator)
    await generator.aclose()
    await asyncio.wait_for(stream.closed.wait(), timeout=0.25)
    await adapter.aclose()
    with pytest.raises(SpeechAdapterClosed):
        await collect(adapter)
    await client.aclose()

    async def empty_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    empty_client = httpx.AsyncClient(transport=httpx.MockTransport(empty_handler))
    empty_adapter = OpenAICompatibleSpeechSynthesizer(
        OpenAICompatibleSpeechConfig(),
        client=empty_client,
    )
    with pytest.raises(SpeechProtocolError, match="empty audio"):
        await collect(empty_adapter)
    await empty_adapter.aclose()
    await empty_client.aclose()


@pytest.mark.asyncio
async def test_adapter_close_releases_active_response_and_rejects_new_work() -> None:
    stream = GatedAudioStream()

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = OpenAICompatibleSpeechSynthesizer(
        OpenAICompatibleSpeechConfig(chunk_size_bytes=4),
        client=client,
    )
    generator = adapter.synthesize(SEGMENT)
    await anext(generator)

    await adapter.aclose()

    await asyncio.wait_for(stream.closed.wait(), timeout=0.25)
    with pytest.raises(SpeechAdapterClosed):
        await collect(adapter)
    await generator.aclose()
    await client.aclose()
