import hashlib
import io
import json
import wave
from pathlib import Path

from app.models import VoiceAsset
from persona.service import LOCAL_WORKSPACE_ID


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
        self.synthesized = []

    def stream_segments(self, text, max_chars=50):
        self.segments = ["第一句。", "第二句。"]
        return self.segments

    def synthesize_to_bytes(self, text, reference_audio=None):
        self.synthesized.append(text)
        return wav_bytes()

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


def test_tts_synthesis_requires_trained_voice(client, tmp_path, monkeypatch):
    persona = client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()
    monkeypatch.setattr("app.routers.tts.AUDIO_ROOT", tmp_path)
    monkeypatch.setattr("app.routers.messages.AUDIO_ROOT", tmp_path)

    response = client.post(
        f"/api/tts/personas/{persona['id']}/conversations/c1/synthesize",
        json={"text": "你好"},
        headers={"X-YUMENO-Request": "web"},
    )

    assert response.status_code == 502
    assert "未绑定训练音色" in response.json()["detail"]


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


def test_tts_incremental_ws_synthesizes_lines_and_persists_one_message(
    client, db_session, tmp_path, monkeypatch
):
    from app.models import ConversationMessage

    with client.app.state.session_factory() as db:
        asset = VoiceAsset(
            name="训练音色",
            engine="gpt_sovits",
            status="ready",
            gpt_weights_path="D:/g.ckpt",
            sovits_weights_path="D:/s.pth",
            refer_audio_path="D:/ref.wav",
            dataset_dir="D:/dataset",
            workspace_id=LOCAL_WORKSPACE_ID,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        asset_id = asset.id
    persona = client.post(
        "/api/personas",
        json={"name": "Voice", "profile": {"tts": {"voice_asset_id": asset_id}}},
    ).json()
    synthesized = []

    class FakeGPT:
        def synthesize(self, text, **kwargs):
            synthesized.append(text)
            return wav_bytes()

        def status(self):
            return {"installed": True}

        def stop_service(self):
            pass

    client.app.state.gpt_sovits = FakeGPT()
    monkeypatch.setattr("app.routers.tts.AUDIO_ROOT", tmp_path / "audio")
    monkeypatch.setattr("app.routers.messages.AUDIO_ROOT", tmp_path / "audio")
    (tmp_path / "audio").mkdir(parents=True, exist_ok=True)

    with client.websocket_connect(
        f"/api/tts/personas/{persona['id']}/conversations/c1/synthesize/ws"
    ) as ws:
        ws.send_json({"type": "text", "text": "第一句。"})
        ws.send_json({"type": "text", "text": "第二句。"})
        ws.send_json({"type": "done"})
        events = []
        for _ in range(10):
            try:
                events.append(ws.receive_json())
            except Exception:
                break

    assert [event["type"] for event in events] == ["segment", "segment", "done"]
    assert synthesized == ["第一句。", "第二句。"]
    assert [event.get("text") for event in events if event["type"] == "segment"] == [
        "第一句。",
        "第二句。",
    ]
    message = events[-1]["message"]
    assert message["role"] == "assistant"
    assert message["kind"] == "audio"
    assert message["content"] == "第一句。第二句。"
    stored = db_session.query(ConversationMessage).one()
    expected_dir = hashlib.sha256(b"c1").hexdigest()[:32]
    assert stored.audio_path.replace("\\", "/").startswith(f"{expected_dir}/")
    assert list((tmp_path / "audio" / expected_dir).glob("*.wav"))


def test_tts_synthesis_routes_to_gpt_sovits_when_persona_binds_trained_voice(
    client, tmp_path, monkeypatch
):
    with client.app.state.session_factory() as db:
        asset = VoiceAsset(
            name="训练音色",
            engine="gpt_sovits",
            status="ready",
            gpt_weights_path="D:/g.ckpt",
            sovits_weights_path="D:/s.pth",
            workspace_id=LOCAL_WORKSPACE_ID,
        )
        db.add(asset)
        db.commit()
        db.refresh(asset)
        asset_id = asset.id
    persona = client.post(
        "/api/personas",
        json={"name": "Voice", "profile": {"tts": {"voice_asset_id": asset_id, "voice_lang": "zh"}}},
    ).json()

    calls = []

    class FakeGPT:
        def synthesize(self, text, **kwargs):
            calls.append((text, kwargs))
            return wav_bytes()

        def stop_service(self):
            pass

    client.app.state.gpt_sovits = FakeGPT()

    def boom(*args, **kwargs):
        raise AssertionError("Lunar worker must not be called for a trained voice")

    client.app.state.tts_factory = lambda: type("T", (), {"synthesize": boom})()
    monkeypatch.setattr("app.routers.tts.AUDIO_ROOT", tmp_path)
    monkeypatch.setattr("app.routers.messages.AUDIO_ROOT", tmp_path)

    response = client.post(
        f"/api/tts/personas/{persona['id']}/conversations/c1/synthesize",
        json={"text": "你好"},
        headers={"X-YUMENO-Request": "web"},
    )

    assert response.status_code == 201
    assert len(calls) == 1
    assert calls[0][0] == "你好"
    assert calls[0][1]["text_lang"] == "zh"
    assert calls[0][1]["gpt_weights"] == "D:/g.ckpt"
