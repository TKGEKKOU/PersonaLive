from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import DocumentJob, Persona
from app.schemas import DocumentJobResponse, PersonaCreate, PersonaResponse, PersonaUpdate
from persona.service import LOCAL_WORKSPACE_ID, PersonaNotFound, create_persona
from settings import Settings

router = APIRouter(prefix="/api/personas", tags=["personas"])


@router.post("", response_model=PersonaResponse, status_code=status.HTTP_201_CREATED)
def create(payload: PersonaCreate, session: Session = Depends(get_session)) -> Persona:
    persona = create_persona(session, payload.name, payload.profile)
    session.commit()
    session.refresh(persona)
    return persona


@router.get("", response_model=list[PersonaResponse])
def list_personas(session: Session = Depends(get_session)) -> list[Persona]:
    statement = (
        select(Persona)
        .where(Persona.workspace_id == LOCAL_WORKSPACE_ID)
        .order_by(Persona.created_at, Persona.id)
    )
    return list(session.scalars(statement))


def local_persona_or_404(session: Session, persona_id: str) -> Persona:
    persona = session.get(Persona, persona_id)
    if persona is None or persona.workspace_id != LOCAL_WORKSPACE_ID:
        raise HTTPException(status_code=404, detail="Persona not found")
    return persona


@router.get("/{persona_id}", response_model=PersonaResponse)
def get_persona(persona_id: str, session: Session = Depends(get_session)) -> Persona:
    return local_persona_or_404(session, persona_id)


@router.patch("/{persona_id}", response_model=PersonaResponse)
def update_persona(
    persona_id: str,
    payload: PersonaUpdate,
    session: Session = Depends(get_session),
) -> Persona:
    persona = local_persona_or_404(session, persona_id)
    if payload.name is not None:
        persona.name = payload.name
    if payload.profile is not None:
        persona.profile_json = {**(persona.profile_json or {}), **payload.profile}
    session.commit()
    session.refresh(persona)
    return persona


@router.delete("/{persona_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_persona(
    persona_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> Response:
    try:
        request.app.state.persona_delete_service.delete(session, persona_id)
    except PersonaNotFound as exc:
        raise HTTPException(status_code=404, detail="Persona not found") from exc
    voice = Settings.load().project_root / "data" / "tts" / "voices" / f"{persona_id}.wav"
    voice.unlink(missing_ok=True)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{persona_id}/documents", response_model=list[DocumentJobResponse])
def list_persona_documents(
    persona_id: str,
    session: Session = Depends(get_session),
) -> list[DocumentJob]:
    persona = local_persona_or_404(session, persona_id)
    statement = (
        select(DocumentJob)
        .where(
            DocumentJob.workspace_id == LOCAL_WORKSPACE_ID,
            DocumentJob.knowledge_space_id == persona.knowledge_space_id,
            DocumentJob.status != "deleted",
        )
        .order_by(DocumentJob.created_at, DocumentJob.id)
    )
    return list(session.scalars(statement))
