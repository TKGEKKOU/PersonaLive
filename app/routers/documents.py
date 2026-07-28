from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import DocumentJob
from app.schemas import DocumentJobResponse
from persona.service import LOCAL_WORKSPACE_ID
from ingestion.document_jobs import (
    create_conversion_job,
    get_local_knowledge_space,
    index_document_job,
    prepare_index,
    prepare_retry,
)


router = APIRouter(tags=["documents"])


def get_job_or_404(session: Session, job_id: str) -> DocumentJob:
    job = session.get(DocumentJob, job_id)
    if job is None or job.workspace_id != LOCAL_WORKSPACE_ID:
        raise HTTPException(status_code=404, detail="Document job not found")
    return job


def session_factory_from(request: Request):
    return request.app.state.session_factory


@router.post(
    "/api/knowledge-spaces/{space_id}/documents/upload",
    response_model=list[DocumentJobResponse],
    status_code=status.HTTP_201_CREATED,
)
async def upload_documents(
    space_id: str,
    files: Annotated[list[UploadFile], File(...)],
    session: Session = Depends(get_session),
):
    if not files:
        raise HTTPException(status_code=422, detail="At least one file is required")
    space = get_local_knowledge_space(session, space_id)
    if space is None:
        raise HTTPException(status_code=404, detail="Knowledge space not found")
    jobs = []
    for upload in files:
        try:
            jobs.append(await create_conversion_job(session, space, upload))
        except ValueError as exc:
            if str(exc) == "UNSUPPORTED_FILE_TYPE":
                raise HTTPException(status_code=415, detail="Unsupported file type") from exc
            if str(exc) == "FILE_TOO_LARGE":
                raise HTTPException(status_code=413, detail="File too large") from exc
            raise HTTPException(status_code=422, detail="Document conversion failed") from exc
        except Exception as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return jobs


@router.get("/api/documents/{job_id}", response_model=DocumentJobResponse)
def get_document(job_id: str, session: Session = Depends(get_session)):
    return get_job_or_404(session, job_id)


@router.post("/api/documents/{job_id}/confirm", response_model=DocumentJobResponse)
def confirm_document(
    job_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_session),
):
    job = get_job_or_404(session, job_id)
    try:
        prepare_index(session, job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Invalid document state") from exc
    background_tasks.add_task(index_document_job, job.id, session_factory_from(request))
    return job


@router.post("/api/documents/{job_id}/retry-index", response_model=DocumentJobResponse)
def retry_document(
    job_id: str,
    background_tasks: BackgroundTasks,
    request: Request,
    session: Session = Depends(get_session),
):
    job = get_job_or_404(session, job_id)
    try:
        prepare_retry(session, job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Invalid document state") from exc
    background_tasks.add_task(index_document_job, job.id, session_factory_from(request))
    return job
