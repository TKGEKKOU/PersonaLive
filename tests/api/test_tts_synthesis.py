from pathlib import Path


class FakeTTS:
    def synthesize(self, text, output, reference_audio=None):
        Path(output).write_bytes(b"RIFFaudio")
        return output


def test_tts_synthesis_persists_assistant_audio(client, tmp_path, monkeypatch):
    persona = client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()
    client.app.state.tts_factory = lambda: FakeTTS()
    monkeypatch.setattr("app.routers.tts.AUDIO_ROOT", tmp_path)
    monkeypatch.setattr("app.routers.messages.AUDIO_ROOT", tmp_path)

    response = client.post(
        f"/api/tts/personas/{persona['id']}/conversations/c1/synthesize",
        json={"text": "你好"},
        headers={"X-PersonaLive-Request": "web"},
    )

    assert response.status_code == 201
    message = response.json()
    assert message["role"] == "assistant"
    assert message["kind"] == "audio"
    assert message["content"] == "你好"
    assert client.get(message["audio_url"]).content == b"RIFFaudio"
