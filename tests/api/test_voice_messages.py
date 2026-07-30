def _persona(client):
    return client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()


def test_voice_message_is_persisted_and_restored(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.messages.AUDIO_ROOT", tmp_path)
    persona = _persona(client)
    conversation_id = "conversation-1"

    response = client.post(
        f"/api/personas/{persona['id']}/conversations/{conversation_id}/voice-messages",
        headers={"X-PersonaLive-Request": "web"},
        files={"file": ("recording.webm", b"0123456789", "audio/webm")},
    )

    assert response.status_code == 201
    message = response.json()
    assert message["kind"] == "audio"
    assert message["status"] == "pending"
    assert message["audio_url"].endswith(f"/{message['id']}/audio")
    assert list(tmp_path.rglob("*.webm"))

    history = client.get(
        f"/api/personas/{persona['id']}/conversations/{conversation_id}/messages"
    )
    assert history.status_code == 200
    assert [item["id"] for item in history.json()] == [message["id"]]


def test_voice_audio_supports_range_requests(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.messages.AUDIO_ROOT", tmp_path)
    persona = _persona(client)
    message = client.post(
        f"/api/personas/{persona['id']}/conversations/c1/voice-messages",
        headers={"X-PersonaLive-Request": "web"},
        files={"file": ("recording.webm", b"0123456789", "audio/webm")},
    ).json()

    response = client.get(message["audio_url"], headers={"Range": "bytes=2-5"})
    assert response.status_code == 206
    assert response.content == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"


def test_voice_message_rejects_unsupported_audio(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.messages.AUDIO_ROOT", tmp_path)
    persona = _persona(client)
    response = client.post(
        f"/api/personas/{persona['id']}/conversations/c1/voice-messages",
        headers={"X-PersonaLive-Request": "web"},
        files={"file": ("note.txt", b"not audio", "text/plain")},
    )
    assert response.status_code == 415
    assert not list(tmp_path.rglob("*.*"))
