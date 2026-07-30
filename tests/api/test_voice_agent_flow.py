from agents.service import AgentTurnResult
from voice.asr.base import ASRUpstreamError


class FakeASR:
    def __init__(self, text="你好 world", error=None):
        self.text = text
        self.error = error

    async def transcribe(self, filename, content_type, audio):
        assert filename.endswith(".webm")
        assert content_type == "audio/webm"
        assert audio == b"voice"
        if self.error:
            raise self.error
        return self.text


class FakeAgentService:
    def __init__(self):
        self.questions = []

    def query(self, question, context):
        self.questions.append((question, context.conversation_id))
        return AgentTurnResult(status="completed", answer="文字回复", specialist="conversation")


def _voice_message(client, tmp_path, monkeypatch):
    monkeypatch.setattr("app.routers.messages.AUDIO_ROOT", tmp_path)
    persona = client.post("/api/personas", json={"name": "Voice", "profile": {}}).json()
    message = client.post(
        f"/api/personas/{persona['id']}/conversations/c1/voice-messages",
        headers={"X-PersonaLive-Request": "web"},
        files={"file": ("recording.webm", b"voice", "audio/webm")},
    ).json()
    return persona, message


def test_voice_transcript_is_saved_and_sent_to_agent(client, tmp_path, monkeypatch):
    persona, message = _voice_message(client, tmp_path, monkeypatch)
    agent = FakeAgentService()
    client.app.state.agent_service = agent
    client.app.state.asr_provider_factory = lambda settings: FakeASR()

    response = client.post(
        f"/api/voice-messages/{message['id']}/transcribe",
        headers={"X-PersonaLive-Request": "web"},
    )

    assert response.status_code == 200
    assert response.json()["message"]["status"] == "completed"
    assert response.json()["message"]["transcript"] == "你好 world"
    assert response.json()["turn"]["answer"] == "文字回复"
    assert agent.questions == [("你好 world", "c1")]


def test_failed_transcription_keeps_audio_and_skips_agent(client, tmp_path, monkeypatch):
    persona, message = _voice_message(client, tmp_path, monkeypatch)
    agent = FakeAgentService()
    client.app.state.agent_service = agent
    client.app.state.asr_provider_factory = lambda settings: FakeASR(error=ASRUpstreamError("offline"))

    response = client.post(
        f"/api/voice-messages/{message['id']}/transcribe",
        headers={"X-PersonaLive-Request": "web"},
    )

    assert response.status_code == 502
    history = client.get(f"/api/personas/{persona['id']}/conversations/c1/messages").json()
    assert history[0]["status"] == "failed"
    assert history[0]["audio_url"]
    assert agent.questions == []
