import json

from agents.service import AgentTurnResult


def test_realtime_text_turn_persists_messages(client):
    persona = client.post("/api/personas", json={"name": "RealtimeMemory"}).json()

    class FakeAgentService:
        def stream_query(self, question, context):
            yield {"kind": "token", "text": "好的，"}
            yield {"kind": "token", "text": "记住了"}
            yield {
                "kind": "result",
                "result": AgentTurnResult(
                    status="completed", answer="好的，记住了", specialist="conversation"
                ),
            }

    client.app.state.agent_service = FakeAgentService()
    conversation_id = "conv-rt"
    with client.websocket_connect(
        f"/ws/personas/{persona['id']}/conversations/{conversation_id}"
    ) as ws:
        assert ws.receive_json()["type"] == "session.ready"
        ws.send_text(json.dumps({"type": "text.submit", "question": "记住我叫小明"}))
        seen_deltas = []
        seen_final = False
        for _ in range(20):
            event = ws.receive_json()
            if event.get("type") == "text.delta":
                seen_deltas.append(event["text"])
            if event.get("type") == "text.final":
                seen_final = True
                break
        assert seen_final
        assert seen_deltas, "streaming tokens must arrive before text.final"
        assert "".join(seen_deltas) == "好的，记住了"

    messages = client.get(
        f"/api/personas/{persona['id']}/conversations/{conversation_id}/messages"
    ).json()
    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "记住我叫小明"),
        ("assistant", "好的，记住了"),
    ]


def test_realtime_forwards_stage_events(client):
    persona = client.post("/api/personas", json={"name": "Stage"}).json()

    class FakeAgentService:
        def stream_query(self, question, context):
            yield {"kind": "stage", "stage": "知识agent · 正在检索角色资料…"}
            yield {
                "kind": "result",
                "result": AgentTurnResult(status="completed", answer="完成", specialist="conversation"),
            }

    client.app.state.agent_service = FakeAgentService()
    with client.websocket_connect(
        f"/ws/personas/{persona['id']}/conversations/conv-stage"
    ) as ws:
        assert ws.receive_json()["type"] == "session.ready"
        ws.send_text(json.dumps({"type": "text.submit", "question": "查资料"}))
        seen_stage = False
        for _ in range(20):
            event = ws.receive_json()
            if event.get("type") == "agent.stage":
                seen_stage = True
                assert event["stage"] == "知识agent · 正在检索角色资料…"
            if event.get("type") == "text.final":
                break
        assert seen_stage
