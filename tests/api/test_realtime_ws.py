import json

from agents.service import AgentTurnResult


def test_realtime_text_turn_persists_messages(client):
    persona = client.post("/api/personas", json={"name": "RealtimeMemory"}).json()

    class FakeAgentService:
        def query(self, question, context):
            return AgentTurnResult(status="completed", answer="好的，记住了", specialist="conversation")

    client.app.state.agent_service = FakeAgentService()
    conversation_id = "conv-rt"
    with client.websocket_connect(
        f"/ws/personas/{persona['id']}/conversations/{conversation_id}"
    ) as ws:
        assert ws.receive_json()["type"] == "session.ready"
        ws.send_text(json.dumps({"type": "text.submit", "question": "记住我叫小明"}))
        seen_final = False
        for _ in range(20):
            event = ws.receive_json()
            if event.get("type") == "text.final":
                seen_final = True
                break
        assert seen_final

    messages = client.get(
        f"/api/personas/{persona['id']}/conversations/{conversation_id}/messages"
    ).json()
    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "记住我叫小明"),
        ("assistant", "好的，记住了"),
    ]
