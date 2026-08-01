import json

import numpy as np
from fastapi.testclient import TestClient

from app.main import create_app
from voice.vad.base import VADEvent


class FakeASRManager:
    def __init__(self, error=None):
        self.error = error
        self.ready_calls = 0

    async def ensure_ready(self):
        self.ready_calls += 1
        if self.error:
            raise self.error


class FakeASRProvider:
    def __init__(self, error=None):
        self.manager = FakeASRManager(error)


class ScriptedVAD:
    """Emits one scripted utterance: speech_start at start_at, stop at stop_at."""

    def __init__(self, start_at: int = 1600, stop_at: int = 40000):
        self.start_at = start_at
        self.stop_at = stop_at
        self.seen = 0
        self.emitted = 0

    def reset(self):
        self.seen = 0
        self.emitted = 0

    def process(self, pcm):
        self.seen += pcm.shape[0]
        events = []
        if self.emitted == 0 and self.seen >= self.start_at:
            events.append(VADEvent("speech_start", self.start_at))
            self.emitted = 1
        if self.emitted == 1 and self.seen >= self.stop_at:
            events.append(VADEvent("speech_stop", self.stop_at))
            self.emitted = 2
        return events


class FakeStreamClient:
    def __init__(self):
        self.calls = []
        self.received_samples = 0
        self.final_error = None

    async def connect(self, language=None):
        self.language = language
        self.calls.append("connect")

    async def feed(self, pcm):
        self.calls.append("feed")
        self.received_samples += pcm.shape[0]

    async def partial(self):
        self.calls.append("partial")
        return "你好"

    async def final(self):
        self.calls.append("final")
        if self.final_error:
            raise self.final_error
        return "Chinese", "你好 world"

    async def cancel(self):
        self.calls.append("cancel")

    async def close(self):
        self.calls.append("close")


class FakeStreamClientFactory:
    def __init__(self, client=FakeStreamClient):
        self.client = client
        self.instances = []

    def __call__(self):
        instance = self.client()
        self.instances.append(instance)
        return instance


def _client():
    app = create_app(initialize_database=False)
    app.state.vad_factory = lambda: ScriptedVAD()
    app.state.asr_provider_factory = lambda settings: FakeASRProvider()
    factory = FakeStreamClientFactory()
    app.state.asr_stream_client_factory = factory
    return TestClient(app, base_url="http://localhost"), factory


def test_start_reports_asr_unavailable():
    app = create_app(initialize_database=False)
    app.state.vad_factory = lambda: ScriptedVAD()
    app.state.asr_provider_factory = lambda settings: FakeASRProvider(error=RuntimeError("worker offline"))
    app.state.asr_stream_client_factory = FakeStreamClientFactory()
    with TestClient(app, base_url="http://localhost") as client:
        with client.websocket_connect("/api/voice/stream/ws") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "start"}))
            event = ws.receive_json()
            assert event["type"] == "error"
            assert event["code"] == "asr_unavailable"


def test_stream_partial_then_final():
    client, factory = _client()
    with client:
        with client.websocket_connect("/api/voice/stream/ws") as ws:
            ready = ws.receive_json()
            assert ready["type"] == "session.ready"
            ws.send_text(json.dumps({"type": "start"}))
            assert ws.receive_json()["type"] == "started"

            # silence: crosses speech_start at sample 1600
            ws.send_bytes(b"\x00\x00" * 3200)
            assert ws.receive_json() == {"type": "vad", "state": "speaking"}

            # crosses the first partial threshold (1600 + 0.7s)
            ws.send_bytes(b"\x00\x00" * 12800)
            event = ws.receive_json()
            assert event["type"] == "partial"
            assert event["text"] == "你好"

            # crosses speech_stop at sample 40000
            ws.send_bytes(b"\x00\x00" * 24000)
            assert ws.receive_json()["type"] == "vad"
            event = ws.receive_json()
            assert event["type"] == "final"
            assert event["text"] == "你好 world"

            worker = factory.instances[-1]
            assert worker.received_samples == 40000 - 1600
            assert "partial" in worker.calls
            assert "final" in worker.calls

            ws.send_text(json.dumps({"type": "cancel"}))
            assert ws.receive_json()["type"] == "cancelled"


def test_cancel_mid_utterance_discards_everything():
    client, factory = _client()
    with client:
        with client.websocket_connect("/api/voice/stream/ws") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "start"}))
            ws.receive_json()
            ws.send_bytes(b"\x00\x00" * 3200)
            ws.receive_json()  # vad speaking
            ws.send_text(json.dumps({"type": "cancel"}))
            assert ws.receive_json()["type"] == "cancelled"
            ws.send_text(json.dumps({"type": "finish"}))
            event = ws.receive_json()
            assert event["type"] == "error"
            assert event["code"] == "not_speaking"
            assert factory.instances[-1].calls[-1] == "cancel"


def test_empty_final_reports_error():
    app = create_app(initialize_database=False)
    app.state.vad_factory = lambda: ScriptedVAD(start_at=1600, stop_at=8000)
    app.state.asr_provider_factory = lambda settings: FakeASRProvider()

    class EmptyFinal(FakeStreamClient):
        async def final(self):
            self.calls.append("final")
            return "Chinese", "  "

    factory = FakeStreamClientFactory(client=EmptyFinal)
    app.state.asr_stream_client_factory = factory
    with TestClient(app, base_url="http://localhost") as client:
        with client.websocket_connect("/api/voice/stream/ws") as ws:
            ws.receive_json()
            ws.send_text(json.dumps({"type": "start"}))
            ws.receive_json()
            ws.send_bytes(b"\x00\x00" * 8000)
            ws.receive_json()  # vad speaking
            event = ws.receive_json()
            assert event["type"] == "vad"
            assert event["state"] == "idle"
            event = ws.receive_json()
            assert event["type"] == "error"
            assert event["code"] == "empty"
