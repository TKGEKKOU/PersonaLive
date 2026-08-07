"""Video -> reference voice import pipeline (方案 A) and separator management."""

from __future__ import annotations

from functools import partial
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_session
from app.routers.personas import local_persona_or_404
from app.routers.tts import VOICE_ROOT, normalize_reference_wavs
from app.routers.settings import require_local


router = APIRouter(prefix="/api/tts", tags=["tts-video-clone"])

MAX_VIDEO_BYTES = 400 * 1024 * 1024
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".mov", ".m4a", ".avi"})


def protected(request: Request, header: str) -> None:
    require_local(request)
    if header != "web":
        raise HTTPException(status_code=403, detail="Missing same-origin request header")


def apply_clone_result(app, persona_id: str, reference_path: Path) -> None:
    """Persist the pipeline output as the persona's reference voice."""
    audio = normalize_reference_wavs([reference_path.read_bytes()])
    VOICE_ROOT.mkdir(parents=True, exist_ok=True)
    target = VOICE_ROOT / f"{persona_id}.wav"
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(audio)
    temporary.replace(target)
    session = app.state.session_factory()
    try:
        persona = local_persona_or_404(session, persona_id)
        profile = dict(persona.profile_json or {})
        tts = dict(profile.get("tts") or {})
        tts.update({"enabled": True, "reference_audio": target.name, "reference_audio_count": 1})
        profile["tts"] = tts
        persona.profile_json = profile
        session.commit()
    finally:
        session.close()


@router.post(
    "/personas/{persona_id}/reference/from-video",
    status_code=status.HTTP_202_ACCEPTED,
)
def import_reference_from_video(
    persona_id: str,
    request: Request,
    video: UploadFile | None = File(default=None),
    x_yumeno_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    protected(request, x_yumeno_request)
    local_persona_or_404(session, persona_id)
    if video is None:
        raise HTTPException(status_code=422, detail="Video file is required")
    suffix = Path(video.filename or "").suffix.lower()
    if suffix not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported video format")
    payload = video.file.read(MAX_VIDEO_BYTES + 1)
    if len(payload) > MAX_VIDEO_BYTES:
        raise HTTPException(status_code=413, detail="Video is too large (max 400 MB)")
    uploads = request.app.state.clone_tasks.tasks_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    target = uploads / f"{uuid4().hex}{suffix}"
    target.write_bytes(payload)
    task_id = request.app.state.clone_tasks.start(
        target,
        apply_result=partial(apply_clone_result, request.app, persona_id),
    )
    return {
        "task_id": task_id,
        "persona_id": persona_id,
        "status_url": f"/api/tts/clone-tasks/{task_id}",
    }


@router.get("/clone-tasks/{task_id}")
def get_clone_task(
    task_id: str,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    record = request.app.state.clone_tasks.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return {
        "task_id": record["task_id"],
        "state": record["state"],
        "phase": record["phase"],
        "progress": record["progress"],
        "error": record.get("error", ""),
        "duration_seconds": record.get("duration_seconds"),
        "segment_count": record.get("segment_count"),
        "status_url": f"/api/tts/clone-tasks/{task_id}",
    }


@router.delete("/clone-tasks/{task_id}")
def cancel_clone_task(
    task_id: str,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    manager = request.app.state.clone_tasks
    record = manager.get(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Task not found")
    cancelled = manager.cancel(task_id)
    if record["state"] in ("succeeded", "failed", "cancelled"):
        manager.cleanup(task_id)
    return {"task_id": task_id, "cancelled": cancelled, "state": record["state"]}


@router.get("/separator/status")
def separator_status(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    return request.app.state.separator_resources.status()


@router.post("/separator/install", status_code=status.HTTP_202_ACCEPTED)
def separator_install(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    request.app.state.separator_resources.start_install()
    return request.app.state.separator_resources.status()


@router.delete("/separator/install/cancel", status_code=status.HTTP_202_ACCEPTED)
def separator_cancel_install(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    request.app.state.separator_resources.cancel_install()
    return request.app.state.separator_resources.status()


@router.delete("/separator/install")
def separator_remove(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    return request.app.state.separator_resources.remove_models()


@router.get("/separator/model-directory")
def separator_model_directory(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    return request.app.state.separator_resources.open_model_directory()
