import asyncio
import json

import numpy as np
import pytest

from voice.asr.stream_client import WorkerStreamClient


class FakeWebSocket:
    """Scripted stand-in for websockets.ClientConnection."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

    async def send(self, payload):
        self.sent.append(payload)

    async def recv(self):
        if not self.responses:
            raise AssertionError("recv called with no scripted response left")
        return self.responses.pop(0)

    async def close(self):
        pass


def test_stream_client_sends_start_and_roundtrips(monkeypatch):
    ws = FakeWebSocket(
        [
            json.dumps({"type": "ready", "sample_rate": 16000}),
            json.dumps({"type": "started"}),
            json.dumps({"type": "partial", "text": "你好"}),
            json.dumps({"type": "final", "text": "你好 world", "language": "Chinese"}),
        ]
    )

    async def fake_connect(url, **kwargs):
        assert url == "ws://127.0.0.1:17004/ws/transcribe"
        return ws

    monkeypatch.setattr("voice.asr.stream_client.websockets.connect", fake_connect)

    async def run():
        client = WorkerStreamClient()
        await client.connect()
        await client.feed(np.zeros(8000, dtype=np.float32))
        partial = await client.partial()
        final = await client.final()
        await client.close()
        return client, partial, final

    client, partial, final = asyncio.run(run())
    commands = [json.loads(item)["type"] for item in ws.sent if isinstance(item, str)]
    assert commands == ["start", "partial", "final"]
    assert partial == "你好"
    assert final == ("Chinese", "你好 world")
    assert any(isinstance(item, bytes) for item in ws.sent), "PCM must be sent as binary"


def test_stream_client_rejects_non_started_response(monkeypatch):
    ws = FakeWebSocket(
        [
            json.dumps({"type": "ready", "sample_rate": 16000}),
            json.dumps({"type": "error", "code": "not_started"}),
        ]
    )
    async def fake_connect(url, **kwargs):
        return ws

    monkeypatch.setattr("voice.asr.stream_client.websockets.connect", fake_connect)

    async def run():
        client = WorkerStreamClient()
        await client.connect()

    with pytest.raises(Exception, match="start"):
        asyncio.run(run())


def test_stream_client_sends_language_in_start(monkeypatch):
    ws = FakeWebSocket(
        [
            json.dumps({"type": "ready", "sample_rate": 16000}),
            json.dumps({"type": "started"}),
        ]
    )
    async def fake_connect(url, **kwargs):
        return ws

    monkeypatch.setattr("voice.asr.stream_client.websockets.connect", fake_connect)

    async def run():
        client = WorkerStreamClient()
        await client.connect(language="Chinese")
        await client.close()
        return client

    asyncio.run(run())
    start = json.loads(ws.sent[0])
    assert start == {"type": "start", "language": "Chinese"}
