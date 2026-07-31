import io
import wave


def wav_audio(frames: bytes, rate: int = 16000) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(frames)
    return stream.getvalue()


def test_upload_reference_voice_updates_persona(client, tmp_path, monkeypatch):
    persona = client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()
    monkeypatch.setattr("app.routers.tts.VOICE_ROOT", tmp_path)
    response = client.post(
        f"/api/tts/personas/{persona['id']}/reference",
        files={"file": ("voice.wav", wav_audio(b"\x00\x00" * 1600), "audio/wav")},
        headers={"X-PersonaLive-Request": "web"},
    )
    assert response.status_code == 200
    updated = client.get(f"/api/personas/{persona['id']}").json()
    assert updated["profile"]["tts"]["enabled"] is True
    with wave.open(str(tmp_path / f"{persona['id']}.wav"), "rb") as reference:
        assert reference.getnchannels() == 1
        assert reference.getsampwidth() == 2
        assert reference.getframerate() == 24000


def test_reference_voice_rejects_non_wav(client, tmp_path, monkeypatch):
    persona = client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()
    monkeypatch.setattr("app.routers.tts.VOICE_ROOT", tmp_path)
    response = client.post(
        f"/api/tts/personas/{persona['id']}/reference",
        files={"file": ("voice.wav", b"not-wave", "audio/wav")},
        headers={"X-PersonaLive-Request": "web"},
    )
    assert response.status_code == 415


def test_reference_voice_can_be_read_and_removed(client, tmp_path, monkeypatch):
    persona = client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()
    monkeypatch.setattr("app.routers.tts.VOICE_ROOT", tmp_path)
    headers = {"X-PersonaLive-Request": "web"}
    uploaded = client.post(
        f"/api/tts/personas/{persona['id']}/reference",
        files={"file": ("voice.wav", wav_audio(b"\x00\x00" * 1600), "audio/wav")},
        headers=headers,
    )
    assert uploaded.status_code == 200
    current = client.get(f"/api/tts/personas/{persona['id']}/reference", headers=headers)
    assert current.status_code == 200
    assert current.json()["name"] == f"{persona['id']}.wav"
    removed = client.delete(f"/api/tts/personas/{persona['id']}/reference", headers=headers)
    assert removed.status_code == 200
    assert removed.json()["configured"] is False
    assert not (tmp_path / f"{persona['id']}.wav").exists()


def test_reference_voice_preview_uses_persona_voice(client, tmp_path, monkeypatch):
    persona = client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()
    monkeypatch.setattr("app.routers.tts.VOICE_ROOT", tmp_path)
    target = tmp_path / f"{persona['id']}.wav"
    target.write_bytes(wav_audio(b"\x00\x00" * 1600))
    client.patch(f"/api/personas/{persona['id']}", json={"profile": {"tts": {"enabled": True, "reference_audio": target.name}}})

    class FakeTTS:
        def synthesize(self, text, output, reference_audio=None):
            assert text == "示例文案"
            assert reference_audio == target
            output.write_bytes(wav_audio(b"\x00\x00"))

    monkeypatch.setattr("app.routers.tts.reference_path", lambda _: target)
    client.app.state.tts_resources.status = lambda: {"ready": True}
    client.app.state.tts_factory = lambda: FakeTTS()
    response = client.post(
        f"/api/tts/personas/{persona['id']}/reference/preview",
        json={"text": "示例文案"},
        headers={"X-PersonaLive-Request": "web"},
    )
    assert response.status_code == 200


def test_multiple_reference_wavs_are_merged(client, tmp_path, monkeypatch):
    persona = client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()
    monkeypatch.setattr("app.routers.tts.VOICE_ROOT", tmp_path)
    response = client.post(
        f"/api/tts/personas/{persona['id']}/reference",
        files=[
            ("files", ("one.wav", wav_audio(b"\x00\x00" * 1600), "audio/wav")),
            ("files", ("two.wav", wav_audio(b"\x01\x00" * 1600), "audio/wav")),
        ],
        headers={"X-PersonaLive-Request": "web"},
    )
    assert response.status_code == 200
    with wave.open(str(tmp_path / f"{persona['id']}.wav"), "rb") as merged:
        assert merged.getframerate() == 24000
        assert 4796 <= merged.getnframes() <= 4800
