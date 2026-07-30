class RecordingCheckpointer:
    def __init__(self):
        self.deleted = []

    def delete_thread(self, thread_id):
        self.deleted.append(thread_id)


def test_clear_conversation_deletes_messages_audio_and_checkpoint(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.messages.AUDIO_ROOT", tmp_path)
    persona = client.post("/api/personas", json={"name": "Clear", "profile": {}}).json()
    conversation_id = "clear-me"
    client.post(
        f"/api/personas/{persona['id']}/conversations/{conversation_id}/voice-messages",
        headers={"X-PersonaLive-Request": "web"},
        files={"file": ("recording.webm", b"voice", "audio/webm")},
    )
    checkpointer = RecordingCheckpointer()
    client.app.state.agent_service.checkpointer = checkpointer

    response = client.delete(
        f"/api/personas/{persona['id']}/conversations/{conversation_id}",
        headers={"X-PersonaLive-Request": "web"},
    )

    assert response.status_code == 204
    assert checkpointer.deleted == [f"{persona['id']}:{conversation_id}"]
    assert client.get(
        f"/api/personas/{persona['id']}/conversations/{conversation_id}/messages"
    ).json() == []
    assert not list(tmp_path.rglob("*.webm"))
