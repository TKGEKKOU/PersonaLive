from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.routers.settings import require_local
from app.schemas import TranscriptionResponse
from settings import Settings
from voice.asr.base import ASRConfigurationError, ASREmptyResultError, ASRUpstreamError


router = APIRouter(prefix="/api/voice", tags=["voice"])
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_AUDIO_BYTES + 64 * 1024
SUPPORTED_AUDIO_TYPES = frozenset(
    {
        "audio/wav",
        "audio/x-wav",
        "audio/webm",
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/m4a",
        "audio/x-m4a",
    }
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


async def read_bounded_body(request: Request) -> bytes:
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            if int(declared_length) > MAX_REQUEST_BYTES:
                raise HTTPException(status_code=413, detail="Audio request is too large")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="Audio request is too large")
        body.extend(chunk)
    if not body:
        raise HTTPException(status_code=422, detail="Audio request is empty")
    return bytes(body)


async def replay_body(body: bytes):
    yield body


@router.post(
    "/transcriptions",
    response_model=TranscriptionResponse,
    openapi_extra={
        "parameters": [
            {
                "name": "X-PersonaLive-Request",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "enum": ["web"]},
            }
        ],
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                }
            },
        },
    },
)
async def transcribe_audio(request: Request) -> TranscriptionResponse:
    require_local(request)
    if request.headers.get("x-personalive-request") != "web":
        raise HTTPException(status_code=403, detail="Missing same-origin request header")
    body = await read_bounded_body(request)
    try:
        form = await MultiPartParser(
            request.headers,
            replay_body(body),
            max_files=1,
            max_fields=1,
            max_part_size=MAX_REQUEST_BYTES,
        ).parse()
    except MultiPartException as exc:
        raise HTTPException(status_code=400, detail="Invalid audio form") from exc
    file = form.get("file")
    if not isinstance(file, UploadFile):
        await form.close()
        raise HTTPException(status_code=422, detail="Audio file is required")
    filename = Path(file.filename or "recording.webm").name
    content_type = resolved_content_type(file)
    try:
        if not content_type:
            raise HTTPException(status_code=415, detail="Unsupported audio type")
        audio = await file.read(MAX_AUDIO_BYTES + 1)
    finally:
        await form.close()
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file is too large")
    if not audio:
        raise HTTPException(status_code=422, detail="Audio file is empty")
    try:
        provider = request.app.state.asr_provider_factory(Settings.load())
        text = await provider.transcribe(filename, content_type, audio)
    except ASRConfigurationError as exc:
        raise HTTPException(status_code=503, detail="ASR is not configured") from exc
    except ASREmptyResultError as exc:
        raise HTTPException(status_code=422, detail="No speech was recognized") from exc
    except ASRUpstreamError as exc:
        raise HTTPException(status_code=502, detail="Speech transcription failed") from exc
    return TranscriptionResponse(text=text)
