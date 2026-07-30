def test_upload_reference_voice_updates_persona(client, tmp_path, monkeypatch):
    persona = client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()
    monkeypatch.setattr("app.routers.tts.VOICE_ROOT", tmp_path)

    response = client.post(
        f"/api/tts/personas/{persona['id']}/reference",
        files={"file": ("voice.wav", b"RIFF0000WAVEaudio", "audio/wav")},
        headers={"X-PersonaLive-Request": "web"},
    )

    assert response.status_code == 200
    updated = client.get(f"/api/personas/{persona['id']}").json()
    assert updated["profile"]["tts"]["enabled"] is True
    assert (tmp_path / f"{persona['id']}.wav").is_file()


def test_reference_voice_rejects_non_wav(client, tmp_path, monkeypatch):
    persona = client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()
    monkeypatch.setattr("app.routers.tts.VOICE_ROOT", tmp_path)

    response = client.post(
        f"/api/tts/personas/{persona['id']}/reference",
        files={"file": ("voice.wav", b"not-wave", "audio/wav")},
        headers={"X-PersonaLive-Request": "web"},
    )

    assert response.status_code == 415
