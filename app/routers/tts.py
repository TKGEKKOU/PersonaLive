import hashlib
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

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
MAX_REFERENCE_BYTES = 10 * 1024 * 1024


class TTSConfigUpdate(BaseModel):
    enabled: bool | None = None


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
def update_config(payload: TTSConfigUpdate, request: Request, x_personalive_request: str = Header(default="")):
    protected(request, x_personalive_request)
    return request.app.state.tts_resources.configure(**payload.model_dump(exclude_unset=True))


@router.post("/install", status_code=status.HTTP_202_ACCEPTED)
def install(request: Request, x_personalive_request: str = Header(default="")):
    protected(request, x_personalive_request)
    request.app.state.tts_resources.start_install()
    return request.app.state.tts_resources.status()


@router.delete("/install")
def remove(request: Request, x_personalive_request: str = Header(default="")):
    protected(request, x_personalive_request)
    return request.app.state.tts_resources.remove_managed()


@router.post("/personas/{persona_id}/reference", response_model=PersonaResponse)
async def upload_reference(
    persona_id: str,
    request: Request,
    file: UploadFile = File(),
    x_personalive_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    protected(request, x_personalive_request)
    persona = local_persona_or_404(session, persona_id)
    audio = await file.read(MAX_REFERENCE_BYTES + 1)
    if len(audio) > MAX_REFERENCE_BYTES:
        raise HTTPException(status_code=413, detail="Reference audio is too large")
    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        raise HTTPException(status_code=415, detail="Reference audio must be a PCM WAV file")
    VOICE_ROOT.mkdir(parents=True, exist_ok=True)
    target = VOICE_ROOT / f"{persona.id}.wav"
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(audio)
    temporary.replace(target)
    profile = dict(persona.profile_json or {})
    tts = dict(profile.get("tts") or {})
    tts.update({"enabled": True, "reference_audio": target.name})
    profile["tts"] = tts
    persona.profile_json = profile
    session.commit()
    session.refresh(persona)
    return persona


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
    x_personalive_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    protected(request, x_personalive_request)
    persona = local_persona_or_404(session, persona_id)
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="TTS text is empty")
    directory = AUDIO_ROOT / hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:32]
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{uuid4()}.wav"
    try:
        tts_profile = (persona.profile_json or {}).get("tts") or {}
        reference_name = str(tts_profile.get("reference_audio") or "")
        reference = VOICE_ROOT / reference_name if reference_name else None
        if reference is not None and not reference.is_file():
            reference = None
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
