from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.routers.settings import require_local
from app.schemas import TranscriptionResponse
from settings import Settings
from voice.asr.base import ASRConfigurationError, ASREmptyResultError, ASRUpstreamError


router = APIRouter(prefix="/api/voice", tags=["voice"])
MAX_AUDIO_BYTES = 10 * 1024 * 1024
SUPPORTED_AUDIO_TYPES = frozenset(
    {"audio/wav", "audio/x-wav", "audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4", "audio/x-m4a"}
)
EXTENSION_TYPES = {
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
}


def resolved_content_type(upload: UploadFile) -> str:
    content_type = (upload.content_type or "").split(";", 1)[0].strip().lower()
    if content_type in SUPPORTED_AUDIO_TYPES:
        return content_type
    if content_type in {"", "application/octet-stream"}:
        return EXTENSION_TYPES.get(Path(upload.filename or "").suffix.lower(), "")
    return ""


@router.post("/transcriptions", response_model=TranscriptionResponse)
async def transcribe_audio(request: Request, file: Annotated[UploadFile, File(...)]) -> TranscriptionResponse:
    require_local(request)
    content_type = resolved_content_type(file)
    if not content_type:
        raise HTTPException(status_code=415, detail="Unsupported audio type")
    try:
        audio = await file.read(MAX_AUDIO_BYTES + 1)
    finally:
        await file.close()
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file is too large")
    if not audio:
        raise HTTPException(status_code=422, detail="Audio file is empty")
    try:
        provider = request.app.state.asr_provider_factory(Settings.load())
        text = await provider.transcribe(Path(file.filename or "recording.webm").name, content_type, audio)
    except ASRConfigurationError as exc:
        raise HTTPException(status_code=503, detail="ASR is not configured") from exc
    except ASREmptyResultError as exc:
        raise HTTPException(status_code=422, detail="No speech was recognized") from exc
    except ASRUpstreamError as exc:
        raise HTTPException(status_code=502, detail="Speech transcription failed") from exc
    return TranscriptionResponse(text=text)
