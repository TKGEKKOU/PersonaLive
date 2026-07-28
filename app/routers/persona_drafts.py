from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import KnowledgeSpace, Persona, PersonaDraft
from app.schemas import PersonaDraftResponse, PersonaDraftUpdate, PersonaResponse
from ingestion.document_jobs import create_conversion_job, index_document_job, prepare_index
from persona.drafts import (
    analyze_materials,
    confirm_draft,
    create_draft,
    draft_documents,
    fallback_identity,
    get_draft,
    identify_candidates,
)


router = APIRouter(prefix="/api/persona-drafts", tags=["persona-drafts"])


def response_for(session: Session, draft: PersonaDraft) -> PersonaDraftResponse:
    persona = session.get(Persona, draft.persona_id) if draft.persona_id else None
    return PersonaDraftResponse(
        id=draft.id,
        mode=draft.mode,
        persona_type=draft.persona_type,
        candidates=draft.candidates_json,
        selected_candidate_id=draft.selected_candidate_id,
        suggested_name=draft.suggested_name,
        profile=draft.profile_json,
        status=draft.status,
        documents=draft_documents(session, draft),
        persona=PersonaResponse.model_validate(persona) if persona else None,
    )


@router.post("/upload", response_model=PersonaDraftResponse, status_code=status.HTTP_201_CREATED)
async def upload_draft(
    mode: Annotated[Literal["character", "expert"], Form()],
    files: Annotated[list[UploadFile], File(...)],
    session: Session = Depends(get_session),
):
    draft = create_draft(session, mode)
    space = session.get(KnowledgeSpace, draft.knowledge_space_id)
    jobs = []
    for upload in files:
        try:
            jobs.append(await create_conversion_job(session, space, upload))
        except ValueError as exc:
            code = 415 if str(exc) == "UNSUPPORTED_FILE_TYPE" else 413 if str(exc) == "FILE_TOO_LARGE" else 422
            raise HTTPException(status_code=code, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    previews = [job.markdown_preview or "" for job in jobs]
    if mode == "character":
        candidates = identify_candidates(previews)
        if candidates:
            for index, candidate in enumerate(candidates, start=1):
                candidate.setdefault("id", f"candidate-{index}")
            draft.persona_type = "character"
            draft.candidates_json = candidates
        else:
            fallback = fallback_identity("expert", jobs[0].original_filename)
            draft.suggested_name, draft.profile_json = analyze_materials("expert", previews, fallback)
    else:
        fallback = fallback_identity("expert", jobs[0].original_filename)
        draft.suggested_name, draft.profile_json = analyze_materials("expert", previews, fallback)
    session.commit()
    session.refresh(draft)
    return response_for(session, draft)


@router.get("/{draft_id}", response_model=PersonaDraftResponse)
def read_draft(draft_id: str, session: Session = Depends(get_session)):
    draft = get_draft(session, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Persona draft not found")
    return response_for(session, draft)


@router.patch("/{draft_id}", response_model=PersonaDraftResponse)
def update_draft(draft_id: str, payload: PersonaDraftUpdate, session: Session = Depends(get_session)):
    draft = get_draft(session, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Persona draft not found")
    if draft.status != "draft":
        raise HTTPException(status_code=409, detail="Persona draft is already confirmed")
    draft.suggested_name = payload.name
    draft.profile_json = payload.profile
    session.commit()
    session.refresh(draft)
    return response_for(session, draft)


@router.post("/{draft_id}/candidates/{candidate_id}", response_model=PersonaDraftResponse)
def select_candidate(draft_id: str, candidate_id: str, session: Session = Depends(get_session)):
    draft = get_draft(session, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Persona draft not found")
    if draft.status != "draft":
        raise HTTPException(status_code=409, detail="Persona draft is already confirmed")
    candidate = next((item for item in draft.candidates_json if item.get("id") == candidate_id), None)
    if not candidate:
        raise HTTPException(status_code=404, detail="Persona candidate not found")
    draft.selected_candidate_id = candidate_id
    draft.suggested_name = candidate["name"]
    draft.profile_json = candidate["profile"]
    session.commit()
    session.refresh(draft)
    return response_for(session, draft)


@router.post("/{draft_id}/confirm", response_model=PersonaDraftResponse)
def confirm_persona_draft(
    draft_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_session),
):
    draft = get_draft(session, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Persona draft not found")
    if draft.persona_type == "character" and not draft.selected_candidate_id:
        raise HTTPException(status_code=409, detail="Select a persona candidate before confirmation")
    already_confirmed = bool(draft.persona_id)
    confirm_draft(session, draft)
    if not already_confirmed:
        for job in draft_documents(session, draft):
            if job.status == "preview_ready":
                prepare_index(session, job)
                background_tasks.add_task(index_document_job, job.id, request.app.state.session_factory)
    return response_for(session, draft)
