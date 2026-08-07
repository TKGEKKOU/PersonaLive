"""摘要注入 supervisor prompt 与上下文构建。"""

from agents.context import PersonaAgentContext
from agents.workflow import _supervisor_prompt


def _context(summary="") -> PersonaAgentContext:
    return PersonaAgentContext(
        persona_id="p1",
        workspace_id="w",
        knowledge_space_ids=("ks1",),
        conversation_id="c1",
        persona_name="测试角色",
        persona_type="knowledge_expert",
        persona_profile={"tts": {"enabled": False}},
        conversation_summary=summary,
    )


def test_supervisor_prompt_includes_summary_when_present():
    prompt = _supervisor_prompt(_context("用户喜欢简短回复"))
    assert "<conversation_summary>用户喜欢简短回复</conversation_summary>" in prompt


def test_supervisor_prompt_omits_summary_when_empty():
    prompt = _supervisor_prompt(_context())
    assert "<conversation_summary>" not in prompt


def test_context_for_loads_summary(client, db_session):
    from sqlalchemy import select

    from app.models import ConversationSummary
    from app.routers.agents import context_for

    persona = client.post("/api/personas", json={"name": "Summary"}).json()
    db_session.add(
        ConversationSummary(
            workspace_id=persona["workspace_id"],
            persona_id=persona["id"],
            conversation_id="c1",
            summary="历史摘要",
            summarized_through_count=10,
        )
    )
    db_session.commit()

    request = type("R", (), {})()
    request.app = client.app
    context = context_for(request, db_session, persona["id"], "c1")
    assert context.conversation_summary == "历史摘要"
