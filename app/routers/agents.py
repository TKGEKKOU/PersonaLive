from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.requests import HTTPConnection

from agents.context import PersonaAgentContext
from app.database import get_session
from app.models import Persona
from app.schemas import AgentQueryPayload, AgentResumePayload, AgentTurnResponse
from persona.service import PersonaNotFound, resolve_knowledge_scope


router = APIRouter(prefix="/api/personas", tags=["agents"])


def context_for(
    connection: HTTPConnection,
    session: Session,
    persona_id: str,
    conversation_id: str,
) -> PersonaAgentContext:
    try:
        scope = resolve_knowledge_scope(session, persona_id)
    except PersonaNotFound as exc:
        raise HTTPException(status_code=404, detail="Persona not found") from exc
    persona = session.get(Persona, persona_id)
    return PersonaAgentContext(
        persona_id=persona.id,
        workspace_id=scope.workspace_id,
        knowledge_space_ids=scope.knowledge_space_ids,
        conversation_id=conversation_id,
        persona_name=persona.name,
        persona_type=persona.persona_type,
        persona_profile=persona.profile_json,
        session_factory=connection.app.state.session_factory,
    )


def response_for(result) -> AgentTurnResponse:
    return AgentTurnResponse(
        status=result.status,
        answer=result.answer,
        specialist=result.specialist,
        pending_action=result.pending_action,
        tool_calls=list(result.tool_calls),
        evidence=list(result.evidence),
        trace=list(result.trace),
    )


@router.post("/{persona_id}/agent/query", response_model=AgentTurnResponse)
def query_agent(
    persona_id: str,
    payload: AgentQueryPayload,
    request: Request,
    session: Session = Depends(get_session),
) -> AgentTurnResponse:
    context = context_for(request, session, persona_id, payload.conversation_id)
    result = request.app.state.agent_service.query(payload.question, context)
    return response_for(result)


@router.post("/{persona_id}/agent/resume", response_model=AgentTurnResponse)
def resume_agent(
    persona_id: str,
    payload: AgentResumePayload,
    request: Request,
    session: Session = Depends(get_session),
) -> AgentTurnResponse:
    context = context_for(request, session, persona_id, payload.conversation_id)
    result = request.app.state.agent_service.resume(
        context,
        payload.specialist,
        payload.approved,
    )
    return response_for(result)

