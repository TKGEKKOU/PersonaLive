"""会话摘要触发与清理的 API 行为。"""

from sqlalchemy import select

from app.models import ConversationSummary


def test_query_schedules_summary_after_turn(client, db_session, monkeypatch):
    from agents.service import AgentTurnResult
    from app.routers import agents as agents_router

    persona = client.post("/api/personas", json={"name": "Alpha"}).json()
    calls = []
    monkeypatch.setattr(
        agents_router,
        "schedule_summary_after_turn",
        lambda session_factory, **kwargs: calls.append(kwargs),
    )

    class FakeAgentService:
        def query(self, question, context):
            return AgentTurnResult(
                status="completed", answer="hello", specialist="conversation"
            )

    client.app.state.agent_service = FakeAgentService()
    client.post(
        f"/api/personas/{persona['id']}/agent/query",
        json={"conversation_id": "c1", "question": "你好"},
    )
    assert len(calls) == 1
    assert calls[0]["persona_id"] == persona["id"]
    assert calls[0]["conversation_id"] == "c1"


def test_clear_conversation_deletes_summary(client, db_session):
    persona = client.post("/api/personas", json={"name": "Clear"}).json()
    persona_id = persona["id"]
    db_session.add(
        ConversationSummary(
            workspace_id=persona["workspace_id"],
            persona_id=persona_id,
            conversation_id="c1",
            summary="摘要",
            summarized_through_count=10,
        )
    )
    db_session.commit()

    class RecordingCheckpointer:
        def delete_thread(self, thread_id):
            self.deleted = [thread_id]

    client.app.state.agent_service.checkpointer = RecordingCheckpointer()
    response = client.delete(
        f"/api/personas/{persona_id}/conversations/c1",
        headers={"x-yumeno-request": "web"},
    )
    assert response.status_code == 204
    assert (
        db_session.scalars(
            select(ConversationSummary).where(
                ConversationSummary.persona_id == persona_id,
                ConversationSummary.conversation_id == "c1",
            )
        ).first()
        is None
    )
