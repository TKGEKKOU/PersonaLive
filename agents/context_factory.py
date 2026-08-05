from collections.abc import Callable

from agents.context import PersonaAgentContext
from app.conversation_summary import get_conversation_summary
from app.models import Persona
from persona.service import PersonaNotFound, resolve_knowledge_scope


def persona_agent_context(
    session_factory: Callable,
    persona_id: str,
    conversation_id: str,
) -> PersonaAgentContext:
    with session_factory() as session:
        scope = resolve_knowledge_scope(session, persona_id)
        persona = session.get(Persona, persona_id)
        if persona is None:
            raise PersonaNotFound(persona_id)
        return PersonaAgentContext(
            persona_id=persona.id,
            workspace_id=scope.workspace_id,
            knowledge_space_ids=scope.knowledge_space_ids,
            conversation_id=conversation_id,
            persona_name=persona.name,
            persona_type=persona.persona_type,
            persona_profile=persona.profile_json,
            session_factory=session_factory,
            conversation_summary=get_conversation_summary(
                session, scope.workspace_id, persona.id, conversation_id
            ),
        )


def build_agent_runner(session_factory: Callable, agent_service):
    """把 Agent 查询包装成同步 runner，供插件与 IM 路由复用。"""

    def run(question: str, persona_id: str, conversation_id: str) -> dict:
        context = persona_agent_context(session_factory, persona_id, conversation_id)
        result = agent_service.query(question, context)
        return {
            "status": result.status,
            "answer": result.answer,
            "specialist": result.specialist,
            "pending_action": result.pending_action,
            "tool_calls": list(result.tool_calls),
            "evidence": list(result.evidence),
            "trace": list(result.trace),
        }

    return run
