import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.models import DocumentJob, KnowledgeSpace
from persona.service import LOCAL_WORKSPACE_ID
from settings import Settings
from ingestion.converter import convert_source
from ingestion.indexer import ingest_markdown_file
from ingestion.markdown_parser import DocumentScope


settings = Settings.load()
DATA_DIR = settings.project_root / "data"
MAX_UPLOAD_BYTES = settings.max_upload_mb * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx",
    ".html", ".htm", ".csv", ".json", ".xml", ".txt", ".md",
    ".epub", ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_filename(filename: str) -> str:
    leaf = Path(filename or "upload").name
    cleaned = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]", "_", leaf).strip("._")
    return cleaned[:200] or "upload"


def get_local_knowledge_space(session: Session, space_id: str) -> KnowledgeSpace | None:
    space = session.get(KnowledgeSpace, space_id)
    if space is None or space.workspace_id != LOCAL_WORKSPACE_ID:
        return None
    return space


async def create_conversion_job(
    session: Session,
    space: KnowledgeSpace,
    upload: UploadFile,
) -> DocumentJob:
    safe_name = sanitize_filename(upload.filename or "upload")
    if Path(safe_name).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise ValueError("UNSUPPORTED_FILE_TYPE")

    job_id = str(uuid4())
    document_id = str(uuid4())
    job_dir = DATA_DIR / "staging" / job_id
    source_path = job_dir / safe_name
    markdown_path = job_dir / "preview.md"
    job_dir.mkdir(parents=True, exist_ok=False)
    job = DocumentJob(
        id=job_id,
        workspace_id=LOCAL_WORKSPACE_ID,
        knowledge_space_id=space.id,
        document_id=document_id,
        original_filename=safe_name,
        markdown_filename=f"{Path(safe_name).stem}.md",
        source_path=str(source_path),
        markdown_path=str(markdown_path),
        status="converting",
    )
    session.add(job)
    session.commit()

    total = 0
    try:
        with source_path.open("wb") as destination:
            while chunk := await upload.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise ValueError("FILE_TOO_LARGE")
                destination.write(chunk)
        job.markdown_preview = convert_source(source_path, markdown_path)
        job.status = "preview_ready"
        job.error_message = None
    except Exception as exc:
        job.status = "conversion_failed"
        job.error_message = str(exc)[:2000]
        session.commit()
        raise
    finally:
        await upload.close()

    session.commit()
    session.refresh(job)
    return job


def prepare_index(session: Session, job: DocumentJob) -> DocumentJob:
    if job.status != "preview_ready" or not job.markdown_path:
        raise ValueError("INVALID_DOCUMENT_STATE")
    job.status = "indexing"
    job.error_message = None
    session.commit()
    session.refresh(job)
    return job


def prepare_retry(session: Session, job: DocumentJob) -> DocumentJob:
    if job.status != "index_failed" or not job.markdown_path:
        raise ValueError("INVALID_DOCUMENT_STATE")
    job.status = "indexing"
    job.error_message = None
    session.commit()
    session.refresh(job)
    return job


def index_document_job(job_id: str, session_factory) -> None:
    with session_factory() as session:
        job = session.get(DocumentJob, job_id)
        if job is None or job.workspace_id != LOCAL_WORKSPACE_ID or not job.markdown_path:
            return
        scope = DocumentScope(job.workspace_id, job.knowledge_space_id, job.document_id)
        try:
            ingest_markdown_file(Path(job.markdown_path), scope)
            job.status = "indexed"
            job.indexed_at = utc_now()
            job.error_message = None
        except Exception as exc:
            job.status = "index_failed"
            job.error_message = str(exc)[:2000]
        session.commit()
