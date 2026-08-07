"""Voice studio API: stepwise draft pipeline for named reference voices."""

from __future__ import annotations

import base64
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from starlette.background import BackgroundTask

from app.routers.settings import require_local
from voice.studio import VoiceStudioError


router = APIRouter(prefix="/api/voice-studio", tags=["voice-studio"])

MAX_VIDEO_BYTES = 400 * 1024 * 1024
MAX_AUDIO_BYTES = 200 * 1024 * 1024
MAX_FORM_PART_BYTES = 512 * 1024 * 1024
VIDEO_EXTENSIONS = frozenset({".mp4", ".mkv", ".webm", ".mov", ".m4a", ".avi"})
AUDIO_EXTENSIONS = frozenset(
    {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm", ".wma", ".aiff", ".aif", ".mp4", ".mov", ".mkv"}
)


def protected(request: Request, header: str) -> None:
    require_local(request)
    if header != "web":
        raise HTTPException(status_code=403, detail="Missing same-origin request header")


def manager(request: Request):
    return request.app.state.voice_studio


def _read_upload(file: UploadFile | None, max_bytes: int, extensions: frozenset[str]) -> tuple[str, bytes]:
    if file is None:
        raise HTTPException(status_code=422, detail="File is required")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in extensions:
        raise HTTPException(status_code=415, detail="Unsupported file format")
    payload = file.file.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise HTTPException(status_code=413, detail="File is too large")
    return suffix, payload


def _read_uploads(files: list[UploadFile] | None, max_bytes: int, extensions: frozenset[str], max_files: int = 200) -> list[tuple[str, bytes]]:
    items = files or []
    if not items:
        raise HTTPException(status_code=422, detail="Files are required")
    if len(items) > max_files:
        raise HTTPException(status_code=413, detail=f"最多一次上传 {max_files} 个文件")
    payloads: list[tuple[str, bytes]] = []
    for item in items:
        suffix = Path(item.filename or "").suffix.lower()
        if suffix not in extensions:
            raise HTTPException(status_code=415, detail=f"不支持的文件格式：{item.filename}")
        payload = item.file.read(max_bytes + 1)
        if len(payload) > max_bytes:
            raise HTTPException(status_code=413, detail=f"文件过大：{item.filename}")
        payloads.append((suffix, payload))
    return payloads


async def _form_files(request: Request, field: str = "files") -> list[UploadFile]:
    """Parse multipart uploads with a large per-part limit (starlette's
    default 1 MB cap rejects bulk reference audio)."""

    form = await request.form(max_part_size=MAX_FORM_PART_BYTES)
    # multipart file fields are parsed as UploadFile-like objects; fastapi's
    # UploadFile class identity differs across versions, so no isinstance gate.
    return list(form.getlist(field))


def _store_upload(request: Request, session_id: str, suffix: str, payload: bytes) -> Path:
    uploads = manager(request)._session_dir(session_id) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    target = uploads / f"{uuid4().hex}{suffix}"
    target.write_bytes(payload)
    return target


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    return manager(request).create_session()


@router.get("/sessions")
def list_sessions(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    return {"sessions": manager(request).list_sessions()}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    state = manager(request).session_state(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return state


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    return {"deleted": manager(request).delete_session(session_id)}


@router.post("/sessions/{session_id}/video", status_code=status.HTTP_202_ACCEPTED)
async def upload_video(
    session_id: str,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    video = (await _form_files(request, "video") or [None])[0]
    suffix, payload = _read_upload(video, MAX_VIDEO_BYTES, VIDEO_EXTENSIONS)
    target = _store_upload(request, session_id, suffix, payload)
    return manager(request).start_video_task(session_id, target)


@router.post("/sessions/{session_id}/audio", status_code=status.HTTP_202_ACCEPTED)
async def upload_audio(
    session_id: str,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    files = await _form_files(request)
    targets = [_store_upload(request, session_id, suffix, payload) for suffix, payload in _read_uploads(files, MAX_AUDIO_BYTES, AUDIO_EXTENSIONS)]
    return manager(request).upload_audio_files(session_id, targets)


@router.post("/sessions/{session_id}/separate", status_code=status.HTTP_202_ACCEPTED)
def start_separation(
    session_id: str,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    try:
        return manager(request).start_separation(session_id)
    except VoiceStudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/segments/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_segments(
    session_id: str,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    files = await _form_files(request)
    targets = [_store_upload(request, session_id, suffix, payload) for suffix, payload in _read_uploads(files, MAX_AUDIO_BYTES, AUDIO_EXTENSIONS)]
    try:
        return manager(request).upload_segments(session_id, targets)
    except VoiceStudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/sessions/{session_id}/segments/{segment_index}")
def delete_segment(
    session_id: str,
    segment_index: int,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    return {"deleted": manager(request).delete_segment(session_id, segment_index)}


@router.get("/sessions/{session_id}/segments/{segment_index}/audio")
def segment_audio(
    session_id: str,
    segment_index: int,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    path = manager(request).segment_path(session_id, segment_index)
    if path is None:
        raise HTTPException(status_code=404, detail="Segment not found")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@router.get("/sessions/{session_id}/reference/audio")
def session_reference_audio(
    session_id: str,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    path = manager(request).reference_path(session_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Reference not ready")
    return FileResponse(path, media_type="audio/wav", filename="reference.wav")


class SegmentSelection(BaseModel):
    indices: list[int]


@router.post("/sessions/{session_id}/segments/select")
def select_segments(
    session_id: str,
    payload: SegmentSelection,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    try:
        return manager(request).select_segments(session_id, payload.indices)
    except (VoiceStudioError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/sessions/{session_id}/reference/upload", status_code=status.HTTP_202_ACCEPTED)
async def upload_reference(
    session_id: str,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    audio = (await _form_files(request, "audio") or [None])[0]
    suffix, payload = _read_upload(audio, MAX_AUDIO_BYTES, AUDIO_EXTENSIONS)
    target = _store_upload(request, session_id, suffix, payload)
    try:
        return manager(request).upload_reference(session_id, target)
    except VoiceStudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


class PreviewRequest(BaseModel):
    text: str


@router.post("/sessions/{session_id}/reference/preview")
def preview_reference(
    session_id: str,
    payload: PreviewRequest,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    if not request.app.state.tts_resources.status()["ready"]:
        raise HTTPException(status_code=409, detail="Local TTS is not ready; install the model in Settings first")
    reference = manager(request).reference_path(session_id)
    if reference is None:
        raise HTTPException(status_code=422, detail="请先生成参考音色")
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="TTS text is empty")
    previews = request.app.state.voice_studio.project_root / "data" / "tts" / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    output = previews / f"voice-studio-{uuid4().hex}.wav"
    try:
        request.app.state.tts_factory().synthesize(text, output, reference)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    similarity = None
    try:
        similarity = request.app.state.voice_similarity.similarity(reference, output)
    except Exception:
        similarity = None
    audio = base64.b64encode(output.read_bytes()).decode("ascii")
    return JSONResponse(
        {"audio": audio, "similarity": round(similarity, 4) if similarity is not None else None},
        background=BackgroundTask(output.unlink, missing_ok=True),
    )


class CompleteRequest(BaseModel):
    name: str


@router.post("/sessions/{session_id}/complete")
def complete_session(
    session_id: str,
    payload: CompleteRequest,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    try:
        return manager(request).complete_session(session_id, payload.name)
    except VoiceStudioError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/voices")
def list_voices(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    return {"voices": manager(request).list_voices()}


@router.get("/voices/{voice_id}/audio")
def voice_audio(voice_id: str, request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    path = manager(request).voice_path(voice_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Voice not found")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@router.delete("/voices/{voice_id}")
def delete_voice(voice_id: str, request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    return {"deleted": manager(request).delete_voice(voice_id)}
