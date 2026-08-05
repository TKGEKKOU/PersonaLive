import io
import json
import wave
from pathlib import Path


class FakeTTS:
    def synthesize(self, text, output, reference_audio=None):
        Path(output).write_bytes(b"RIFFaudio")
        return output


def wav_bytes(seconds: float = 1.0) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b"\x00\x00" * int(24000 * seconds))
    return stream.getvalue()


class FakeStreamTTS:
    def __init__(self):
        self.segments = []

    def stream_segments(self, text, max_chars=50):
        self.segments = ["第一句。", "第二句。"]
        return self.segments

    def synthesize(self, text, output, reference_audio=None):
        Path(output).write_bytes(wav_bytes())
        return output

    @staticmethod
    def merge_wavs(parts):
        return b"".join(parts)


def test_tts_preview_returns_generated_audio_without_persona(client, tmp_path, monkeypatch):
    client.app.state.tts_factory = lambda: FakeTTS()
    monkeypatch.setattr(client.app.state.tts_resources, "status", lambda: {"ready": True})
    monkeypatch.setattr("app.routers.tts.TTS_PREVIEW_ROOT", tmp_path)

    response = client.post(
        "/api/tts/preview",
        json={"text": "测试语音"},
        headers={"X-YUMENO-Request": "web"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == b"RIFFaudio"


def test_tts_synthesis_persists_assistant_audio(client, tmp_path, monkeypatch):
    persona = client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()
    client.app.state.tts_factory = lambda: FakeTTS()
    monkeypatch.setattr("app.routers.tts.AUDIO_ROOT", tmp_path)
    monkeypatch.setattr("app.routers.messages.AUDIO_ROOT", tmp_path)

    response = client.post(
        f"/api/tts/personas/{persona['id']}/conversations/c1/synthesize",
        json={"text": "你好"},
        headers={"X-YUMENO-Request": "web"},
    )

    assert response.status_code == 201
    message = response.json()
    assert message["role"] == "assistant"
    assert message["kind"] == "audio"
    assert message["content"] == "你好"
    assert client.get(message["audio_url"]).content == b"RIFFaudio"


def test_tts_stream_synthesis_progressively_returns_segments_and_persists_message(
    client, tmp_path, monkeypatch
):
    persona = client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()
    fake = FakeStreamTTS()
    client.app.state.tts_factory = lambda: fake
    monkeypatch.setattr(client.app.state.tts_resources, "status", lambda: {"ready": True})
    monkeypatch.setattr("app.routers.tts.TTS_PREVIEW_ROOT", tmp_path)
    monkeypatch.setattr("app.routers.tts.AUDIO_ROOT", tmp_path / "audio")
    monkeypatch.setattr("app.routers.messages.AUDIO_ROOT", tmp_path / "audio")
    (tmp_path / "audio").mkdir(parents=True, exist_ok=True)

    response = client.post(
        f"/api/tts/personas/{persona['id']}/conversations/c1/synthesize/stream",
        json={"text": "第一句。第二句。"},
        headers={"X-YUMENO-Request": "web"},
    )

    assert response.status_code == 200
    lines = [json.loads(line) for line in response.text.strip().splitlines() if line.strip()]
    assert [line["type"] for line in lines] == ["segment", "segment", "done"]
    assert fake.segments == ["第一句。", "第二句。"]
    assert [line.get("text") for line in lines if line["type"] == "segment"] == ["第一句。", "第二句。"]
    message = lines[-1]["message"]
    assert message["role"] == "assistant"
    assert message["kind"] == "audio"
    assert message["content"] == "第一句。第二句。"
    audio = client.get(message["audio_url"])
    assert audio.status_code == 200
    assert len(audio.content) > 44
