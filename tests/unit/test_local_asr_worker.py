import asyncio

import httpx
import pytest

from voice.asr.base import ASREmptyResultError, ASRUpstreamError
from voice.asr.local_worker import LocalQwenASR


def test_local_qwen_asr_returns_transcript():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/transcribe"
        assert request.headers["content-type"] == "audio/webm"
        return httpx.Response(200, json={"text": "你好 world"})

    transport = httpx.MockTransport(handler)
    async def run():
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            provider = LocalQwenASR(client=client)
            return await provider.transcribe("recording.webm", "audio/webm", b"audio")

    assert asyncio.run(run()) == "你好 world"


def test_local_qwen_asr_rejects_empty_transcript():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"text": "  "}))
    async def run():
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            return await LocalQwenASR(client=client).transcribe("recording.webm", "audio/webm", b"audio")

    with pytest.raises(ASREmptyResultError):
        asyncio.run(run())


def test_local_qwen_asr_maps_worker_failure():
    transport = httpx.MockTransport(lambda request: httpx.Response(503, json={"detail": "model unavailable"}))
    async def run():
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8765") as client:
            return await LocalQwenASR(client=client).transcribe("recording.webm", "audio/webm", b"audio")

    with pytest.raises(ASRUpstreamError, match="HTTP 503"):
        asyncio.run(run())
