import hashlib
import io
import audioop
import base64
import json
import wave
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.database import get_session
from app.models import ConversationMessage, Persona
from app.routers.messages import message_response
from app.routers.personas import local_persona_or_404
from app.routers.settings import require_local
from app.schemas import ConversationMessageResponse, PersonaResponse
from persona.service import LOCAL_WORKSPACE_ID
from settings import Settings
from voice.tts.local_worker import TTSGenerationError


router = APIRouter(prefix="/api/tts", tags=["tts"])
AUDIO_ROOT = Settings.load().project_root / "data" / "audio"
VOICE_ROOT = Settings.load().project_root / "data" / "tts" / "voices"
TTS_PREVIEW_ROOT = Settings.load().project_root / "data" / "tts" / "previews"
MAX_REFERENCE_BYTES = 10 * 1024 * 1024
REFERENCE_RATE = 24000
# Qwen3-TTS reference audio: 3s minimum, 10~20s works best; over 30s can degrade.
# Multiple uploads are concatenated into one reference; this caps the total length.
MAX_REFERENCE_SECONDS = 20


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def normalize_reference_wavs(payloads: list[bytes]) -> bytes:
    frames = bytearray()
    frame_limit = REFERENCE_RATE * MAX_REFERENCE_SECONDS * 2
    for payload in payloads:
        try:
            with wave.open(io.BytesIO(payload), "rb") as source:
                channels = source.getnchannels()
                width = source.getsampwidth()
                rate = source.getframerate()
                if channels not in (1, 2) or width not in (1, 2, 3, 4) or source.getcomptype() != "NONE":
                    raise ValueError("unsupported WAV format")
                audio = source.readframes(source.getnframes())
        except (wave.Error, EOFError) as exc:
            raise ValueError("invalid WAV file") from exc
        if width == 1:
            audio = audioop.bias(audio, 1, -128)
        if channels == 2:
            audio = audioop.tomono(audio, width, 0.5, 0.5)
        if width != 2:
            audio = audioop.lin2lin(audio, width, 2)
        if rate != REFERENCE_RATE:
            audio, _ = audioop.ratecv(audio, 2, 1, rate, REFERENCE_RATE, None)
        frames.extend(audio[: max(0, frame_limit - len(frames))])
        if len(frames) >= frame_limit:
            break
    output = io.BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(REFERENCE_RATE)
        target.writeframes(frames)
    return output.getvalue()


def reference_path(persona: Persona) -> Path | None:
    name = str(((persona.profile_json or {}).get("tts") or {}).get("reference_audio") or "")
    if not name or Path(name).name != name:
        return None
    path = VOICE_ROOT / name
    return path if path.is_file() else None


class TTSConfigUpdate(BaseModel):
    enabled: bool | None = None
    use_gpu: bool | None = None


class TTSSynthesisRequest(BaseModel):
    text: str


def protected(request: Request, header: str) -> None:
    require_local(request)
    if header != "web":
        raise HTTPException(status_code=403, detail="Missing same-origin request header")


@router.get("/status")
def get_status(request: Request):
    require_local(request)
    return request.app.state.tts_resources.status()


@router.patch("/config")
def update_config(payload: TTSConfigUpdate, request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    values = payload.model_dump(exclude_unset=True)
    if "use_gpu" in values and values["use_gpu"] != request.app.state.tts_resources.config()["use_gpu"]:
        request.app.state.tts_worker.stop_service()
        request.app.state.tts_worker.use_gpu = values["use_gpu"]
    return request.app.state.tts_resources.configure(**values)


@router.post("/install", status_code=status.HTTP_202_ACCEPTED)
def install(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    request.app.state.tts_resources.start_install()
    return request.app.state.tts_resources.status()


@router.delete("/install/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel_install(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    request.app.state.tts_resources.cancel_install()
    return request.app.state.tts_resources.status()


@router.delete("/install")
def remove(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    return request.app.state.tts_resources.remove_models()


@router.post("/model-directory")
def open_model_directory(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    return request.app.state.tts_resources.open_model_directory()


@router.post("/preview")
def preview(payload: TTSSynthesisRequest, request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="TTS text is empty")
    if not request.app.state.tts_resources.status()["ready"]:
        raise HTTPException(status_code=409, detail="Local TTS is not ready")
    TTS_PREVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    output = TTS_PREVIEW_ROOT / f"preview-{uuid4()}.wav"
    try:
        request.app.state.tts_factory().synthesize(text, output)
    except TTSGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return FileResponse(output, media_type="audio/wav", background=BackgroundTask(output.unlink, missing_ok=True))


@router.post("/personas/{persona_id}/reference", response_model=PersonaResponse)
async def upload_reference(
    persona_id: str,
    request: Request,
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    x_yumeno_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    protected(request, x_yumeno_request)
    persona = local_persona_or_404(session, persona_id)
    uploads = files or ([file] if file else [])
    if not uploads:
        raise HTTPException(status_code=422, detail="Reference audio is required")
    payloads = [await item.read(MAX_REFERENCE_BYTES + 1) for item in uploads]
    if sum(len(item) for item in payloads) > MAX_REFERENCE_BYTES:
        raise HTTPException(status_code=413, detail="Reference audio is too large")
    try:
        audio = normalize_reference_wavs(payloads)
    except ValueError as exc:
        raise HTTPException(status_code=415, detail="Reference audio must be an uncompressed PCM WAV file") from exc
    VOICE_ROOT.mkdir(parents=True, exist_ok=True)
    target = VOICE_ROOT / f"{persona.id}.wav"
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(audio)
    temporary.replace(target)
    profile = dict(persona.profile_json or {})
    tts = dict(profile.get("tts") or {})
    tts.update({"enabled": True, "reference_audio": target.name, "reference_audio_count": len(payloads)})
    profile["tts"] = tts
    persona.profile_json = profile
    session.commit()
    session.refresh(persona)
    return persona


@router.get("/personas/{persona_id}/reference")
def get_reference(
    persona_id: str,
    request: Request,
    x_yumeno_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    protected(request, x_yumeno_request)
    persona = local_persona_or_404(session, persona_id)
    path = reference_path(persona)
    if path is None:
        return {"configured": False, "name": None, "max_seconds": MAX_REFERENCE_SECONDS}
    count = int((((persona.profile_json or {}).get("tts") or {}).get("reference_audio_count") or 1))
    size = path.stat().st_size
    duration_seconds = round(max(0.0, (size - 44) / (REFERENCE_RATE * 2)), 1)
    return {
        "configured": True,
        "name": path.name,
        "size": size,
        "count": count,
        "duration_seconds": duration_seconds,
        "max_seconds": MAX_REFERENCE_SECONDS,
    }


@router.get("/personas/{persona_id}/reference/audio")
def play_reference(
    persona_id: str,
    request: Request,
    x_yumeno_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    protected(request, x_yumeno_request)
    persona = local_persona_or_404(session, persona_id)
    path = reference_path(persona)
    if path is None:
        raise HTTPException(status_code=404, detail="Reference audio is not configured")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@router.post("/personas/{persona_id}/reference/preview")
def preview_reference(
    persona_id: str,
    payload: TTSSynthesisRequest,
    request: Request,
    x_yumeno_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    protected(request, x_yumeno_request)
    persona = local_persona_or_404(session, persona_id)
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="TTS text is empty")
    if not request.app.state.tts_resources.status()["ready"]:
        raise HTTPException(status_code=409, detail="Local TTS is not ready; install the model in Settings first")
    reference = reference_path(persona)
    if reference is None:
        raise HTTPException(status_code=404, detail="Reference audio is not configured")
    TTS_PREVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    output = TTS_PREVIEW_ROOT / f"persona-{persona_id}-{uuid4()}.wav"
    try:
        request.app.state.tts_factory().synthesize(text, output, reference)
    except TTSGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return FileResponse(output, media_type="audio/wav", background=BackgroundTask(output.unlink, missing_ok=True))


@router.delete("/personas/{persona_id}/reference")
def remove_reference(
    persona_id: str,
    request: Request,
    x_yumeno_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    protected(request, x_yumeno_request)
    persona = local_persona_or_404(session, persona_id)
    profile = dict(persona.profile_json or {})
    tts = dict(profile.get("tts") or {})
    name = str(tts.pop("reference_audio", "") or "")
    tts.pop("reference_audio_count", None)
    if name and Path(name).name == name:
        path = VOICE_ROOT / name
        path.unlink(missing_ok=True)
    profile["tts"] = tts
    persona.profile_json = profile
    session.commit()
    return {"configured": False, "name": None}


@router.post(
    "/personas/{persona_id}/conversations/{conversation_id}/synthesize",
    response_model=ConversationMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def synthesize(
    persona_id: str,
    conversation_id: str,
    payload: TTSSynthesisRequest,
    request: Request,
    x_yumeno_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    protected(request, x_yumeno_request)
    persona = local_persona_or_404(session, persona_id)
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="TTS text is empty")
    directory = AUDIO_ROOT / hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:32]
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{uuid4()}.wav"
    try:
        tts_profile = (persona.profile_json or {}).get("tts") or {}
        reference = reference_path(persona)
        request.app.state.tts_factory().synthesize(text, output, reference)
    except TTSGenerationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    message = ConversationMessage(
        workspace_id=LOCAL_WORKSPACE_ID,
        persona_id=persona_id,
        conversation_id=conversation_id,
        role="assistant",
        kind="audio",
        content=text,
        audio_path=str(output.relative_to(AUDIO_ROOT)),
        audio_content_type="audio/wav",
        status="completed",
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message_response(message)


@router.post("/personas/{persona_id}/conversations/{conversation_id}/synthesize/stream")
def synthesize_stream(
    persona_id: str,
    conversation_id: str,
    payload: TTSSynthesisRequest,
    request: Request,
    x_yumeno_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    """流式语音合成：文本按句子切分，逐段合成并推送，最后持久化一条完整消息。

    响应为 NDJSON（application/x-ndjson），每行一个事件：
      {"type":"segment","index":0,"audio":"<base64 wav>"}
      {"type":"done","message":{...}}
      {"type":"error","message":"..."}
    """
    protected(request, x_yumeno_request)
    persona = local_persona_or_404(session, persona_id)
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="TTS text is empty")
    if not request.app.state.tts_resources.status().get("ready"):
        raise HTTPException(status_code=409, detail="Local TTS is not ready")
    directory = AUDIO_ROOT / hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:32]
    directory.mkdir(parents=True, exist_ok=True)
    TTS_PREVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    worker = request.app.state.tts_factory()
    reference = reference_path(persona)
    segments = worker.stream_segments(text)

    def event_source():
        parts: list[bytes] = []
        try:
            for index, segment in enumerate(segments):
                temporary = TTS_PREVIEW_ROOT / f"stream-{uuid4()}.wav"
                try:
                    worker.synthesize(segment, temporary, reference)
                    audio = temporary.read_bytes()
                finally:
                    temporary.unlink(missing_ok=True)
                parts.append(audio)
                yield json.dumps(
                    {
                        "type": "segment",
                        "index": index,
                        "text": segment,
                        "audio": base64.b64encode(audio).decode("ascii"),
                    },
                    ensure_ascii=False,
                ) + "\n"
            merged = worker.merge_wavs(parts)
            output = directory / f"{uuid4()}.wav"
            output.write_bytes(merged)
            db = request.app.state.session_factory()
            try:
                message = ConversationMessage(
                    workspace_id=LOCAL_WORKSPACE_ID,
                    persona_id=persona_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    kind="audio",
                    content=text,
                    audio_path=str(output.relative_to(AUDIO_ROOT)),
                    audio_content_type="audio/wav",
                    status="completed",
                )
                db.add(message)
                db.commit()
                db.refresh(message)
                payload_message = message_response(message)
            finally:
                db.close()
            yield json.dumps(
                {"type": "done", "message": payload_message}, ensure_ascii=False, default=_json_default
            ) + "\n"
        except TTSGenerationError as exc:
            yield json.dumps(
                {"type": "error", "message": str(exc)}, ensure_ascii=False, default=_json_default
            ) + "\n"
        except Exception as exc:  # noqa: BLE001 - 流式响应需要把错误推给前端
            yield json.dumps(
                {"type": "error", "message": str(exc)}, ensure_ascii=False, default=_json_default
            ) + "\n"

    return StreamingResponse(event_source(), media_type="application/x-ndjson")
