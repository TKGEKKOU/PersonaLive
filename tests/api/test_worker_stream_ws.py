from types import SimpleNamespace

import numpy as np
from fastapi.testclient import TestClient

from voice.asr.worker_server import create_worker_app


class FakeModel:
    def __init__(self):
        self.transcribe_calls = []

    def transcribe(self, audio, language=None):
        if isinstance(audio, tuple):
            assert isinstance(audio[0], np.ndarray), "streaming audio must be (pcm, sr)"
        self.last_language = language
        self.transcribe_calls.append(audio)
        return [SimpleNamespace(language="Chinese", text="你好 world")]


def test_worker_stream_partial_and_final():
    fake = FakeModel()
    app = create_worker_app(model_provider=lambda: fake)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/transcribe") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_text('{"type":"start"}')
            assert ws.receive_json()["type"] == "started"
            ws.send_bytes(b"\x00\x00" * 16000)
            ws.send_text('{"type":"partial"}')
            event = ws.receive_json()
            assert event["type"] == "partial"
            assert event["text"] == "你好 world"
            ws.send_text('{"type":"final"}')
            event = ws.receive_json()
            assert event["type"] == "final"
            assert event["language"] == "Chinese"
            assert event["text"] == "你好 world"
            assert fake.transcribe_calls, "model should have been called"
            assert isinstance(fake.transcribe_calls[-1], tuple)


def test_worker_stream_audio_before_start_is_rejected():
    app = create_worker_app(model_provider=lambda: FakeModel())
    with TestClient(app) as client:
        with client.websocket_connect("/ws/transcribe") as ws:
            ws.receive_json()
            ws.send_bytes(b"\x00\x00" * 512)
            event = ws.receive_json()
            assert event["type"] == "error"
            assert event["code"] == "not_started"


def test_worker_stream_forwards_language():
    fake = FakeModel()
    app = create_worker_app(model_provider=lambda: fake)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/transcribe") as ws:
            ws.receive_json()
            ws.send_text('{"type":"start","language":"Chinese"}')
            ws.receive_json()
            ws.send_bytes(b"\x00\x00" * 512)
            ws.send_text('{"type":"partial"}')
            ws.receive_json()
    assert fake.last_language == "Chinese"


def test_worker_stream_cancel_and_unknown_command():
    app = create_worker_app(model_provider=lambda: FakeModel())
    with TestClient(app) as client:
        with client.websocket_connect("/ws/transcribe") as ws:
            ws.receive_json()
            ws.send_text('{"type":"start"}')
            ws.receive_json()
            ws.send_text('{"type":"cancel"}')
            assert ws.receive_json()["type"] == "cancelled"
            ws.send_text('{"type":"bogus"}')
            event = ws.receive_json()
            assert event["type"] == "error"
            assert event["code"] == "unknown_command"


def test_worker_http_transcribe_still_works():
    fake = FakeModel()
    app = create_worker_app(model_provider=lambda: fake)
    with TestClient(app) as client:
        response = client.post("/transcribe", content=b"audio-bytes")
        assert response.status_code == 200
        assert response.json() == {"text": "你好 world"}
