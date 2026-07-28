from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import Persona
from app.schemas import RagQueryPayload, RagQueryResponse
from persona.service import PersonaNotFound, resolve_knowledge_scope
from settings import Settings
from rag.contracts import RagQueryContext
from rag.service import RagRequest, create_rag_service


router = APIRouter(prefix="/api/personas", tags=["rag"])


@router.post("/{persona_id}/rag/query", response_model=RagQueryResponse)
def query_persona(
    persona_id: str,
    payload: RagQueryPayload,
    session: Session = Depends(get_session),
) -> RagQueryResponse:
    settings = Settings.load()
    try:
        scope = resolve_knowledge_scope(session, persona_id)
    except PersonaNotFound as exc:
        raise HTTPException(status_code=404, detail="Persona not found") from exc

    context = RagQueryContext(
        persona_id=persona_id,
        workspace_id=scope.workspace_id,
        knowledge_space_ids=scope.knowledge_space_ids,
        conversation_id=payload.conversation_id,
    )
    persona = session.get(Persona, persona_id)
    result = create_rag_service(settings).query(
        RagRequest(
            question=payload.question,
            context=context,
            allow_web_fallback=settings.enable_web_fallback,
            persona_name=persona.name,
            persona_profile=persona.profile_json,
        )
    )
    return RagQueryResponse(
        answer=result.answer_draft,
        evidence=list(result.evidence),
        confidence=result.confidence,
        used_web_search=result.used_web_search,
        trace=list(result.trace),
        grounded=result.grounded,
        useful=result.useful,
        missing_points=list(result.missing_points),
        interaction_mode=result.interaction_mode,
    )
