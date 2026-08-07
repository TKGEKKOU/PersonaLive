from agents.service import AgentTurnResult


def test_agent_query_uses_server_persona_context(client, monkeypatch):
    persona = client.post("/api/personas", json={"name": "Alpha"}).json()
    captured = {}

    class FakeAgentService:
        def query(self, question, context):
            captured["context"] = context
            return AgentTurnResult(status="completed", answer="hello", specialist="conversation")

    client.app.state.agent_service = FakeAgentService()
    response = client.post(
        f"/api/personas/{persona['id']}/agent/query",
        json={"question": "你好", "conversation_id": "conversation-a"},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "hello"
    assert captured["context"].persona_id == persona["id"]
    assert captured["context"].knowledge_space_ids == (persona["knowledge_space_id"],)


def test_agent_resume_passes_only_server_context_and_user_decision(client):
    persona = client.post("/api/personas", json={"name": "Alpha"}).json()
    captured = {}

    class FakeAgentService:
        def resume(self, context, specialist, approved):
            captured.update(context=context, specialist=specialist, approved=approved)
            return AgentTurnResult(status="completed", answer="已取消", specialist=specialist)

    client.app.state.agent_service = FakeAgentService()
    response = client.post(
        f"/api/personas/{persona['id']}/agent/resume",
        json={"conversation_id": "conversation-a", "specialist": "management", "approved": False},
    )

    assert response.status_code == 200
    assert captured["context"].persona_id == persona["id"]
    assert captured["specialist"] == "management"
    assert captured["approved"] is False


def test_agent_stream_returns_sse_events(client):
    persona = client.post("/api/personas", json={"name": "Alpha"}).json()

    class FakeAgentService:
        def stream_query(self, question, context):
            yield {"kind": "stage", "stage": "知识agent · 正在检索角色资料…"}
            yield {"kind": "token", "text": "你好"}
            yield {
                "kind": "result",
                "result": AgentTurnResult(
                    status="completed", answer="你好", specialist="conversation"
                ),
            }

    client.app.state.agent_service = FakeAgentService()
    response = client.post(
        f"/api/personas/{persona['id']}/agent/stream",
        json={"question": "资料", "conversation_id": "conversation-a"},
        headers={"X-YUMENO-Request": "web"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    text = response.text
    assert "知识agent · 正在检索角色资料…" in text
    assert '"kind": "token"' in text
    assert '"kind": "done"' in text


def test_agent_query_persists_text_turn(client):
    persona = client.post("/api/personas", json={"name": "Memory"}).json()

    class FakeAgentService:
        def query(self, question, context):
            return AgentTurnResult(status="completed", answer="记住了", specialist="conversation")

    client.app.state.agent_service = FakeAgentService()
    response = client.post(
        f"/api/personas/{persona['id']}/agent/query",
        json={"question": "记住我叫小明", "conversation_id": "conv-1"},
    )
    assert response.status_code == 200

    messages = client.get(
        f"/api/personas/{persona['id']}/conversations/conv-1/messages"
    ).json()
    assert [(message["role"], message["content"]) for message in messages] == [
        ("user", "记住我叫小明"),
        ("assistant", "记住了"),
    ]
