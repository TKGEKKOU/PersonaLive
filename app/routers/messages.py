import asyncio
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import ConversationMessage
from app.routers.agents import context_for, response_for
from app.routers.personas import local_persona_or_404
from app.routers.settings import require_local
from app.schemas import ConversationMessageResponse, VoiceMessageTurnResponse
from persona.service import LOCAL_WORKSPACE_ID
from settings import Settings
from voice.asr.base import ASREmptyResultError, ASRUpstreamError


router = APIRouter(tags=["messages"])
AUDIO_ROOT = Settings.load().project_root / "data" / "audio"
MAX_AUDIO_BYTES = 10 * 1024 * 1024
CONTENT_EXTENSIONS = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


def message_response(message: ConversationMessage) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "kind": message.kind,
        "content": message.content,
        "audio_url": f"/api/voice-messages/{message.id}/audio" if message.audio_path else None,
        "transcript": message.transcript,
        "status": message.status,
        "error_message": message.error_message,
        "created_at": message.created_at,
    }


@router.post(
    "/api/personas/{persona_id}/conversations/{conversation_id}/voice-messages",
    response_model=ConversationMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_voice_message(
    persona_id: str,
    conversation_id: str,
    file: UploadFile = File(),
    x_personalive_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    if x_personalive_request != "web":
        raise HTTPException(status_code=403, detail="Missing same-origin request header")
    local_persona_or_404(session, persona_id)
    content_type = (file.content_type or "").split(";", 1)[0].lower()
    extension = CONTENT_EXTENSIONS.get(content_type)
    if extension is None:
        raise HTTPException(status_code=415, detail="Unsupported audio type")
    audio = await file.read(MAX_AUDIO_BYTES + 1)
    if not audio:
        raise HTTPException(status_code=422, detail="Audio file is empty")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file is too large")

    message = ConversationMessage(
        workspace_id=LOCAL_WORKSPACE_ID,
        persona_id=persona_id,
        conversation_id=conversation_id,
        role="user",
        kind="audio",
        audio_content_type=content_type,
        status="pending",
    )
    session.add(message)
    session.flush()
    directory = AUDIO_ROOT / hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:32]
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{message.id}{extension}"
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".audio-", delete=False) as temporary:
        temporary.write(audio)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, target)
    message.audio_path = str(target.relative_to(AUDIO_ROOT))
    session.commit()
    session.refresh(message)
    return message_response(message)


@router.get(
    "/api/personas/{persona_id}/conversations/{conversation_id}/messages",
    response_model=list[ConversationMessageResponse],
)
def list_messages(persona_id: str, conversation_id: str, session: Session = Depends(get_session)):
    local_persona_or_404(session, persona_id)
    statement = (
        select(ConversationMessage)
        .where(
            ConversationMessage.workspace_id == LOCAL_WORKSPACE_ID,
            ConversationMessage.persona_id == persona_id,
            ConversationMessage.conversation_id == conversation_id,
        )
        .order_by(ConversationMessage.created_at, ConversationMessage.id)
    )
    return [message_response(message) for message in session.scalars(statement)]


@router.delete(
    "/api/personas/{persona_id}/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def clear_conversation(
    persona_id: str,
    conversation_id: str,
    request: Request,
    x_personalive_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    if x_personalive_request != "web":
        raise HTTPException(status_code=403, detail="Missing same-origin request header")
    local_persona_or_404(session, persona_id)
    statement = select(ConversationMessage).where(
        ConversationMessage.workspace_id == LOCAL_WORKSPACE_ID,
        ConversationMessage.persona_id == persona_id,
        ConversationMessage.conversation_id == conversation_id,
    )
    messages = list(session.scalars(statement))
    thread_id = f"{persona_id}:{conversation_id}"
    request.app.state.agent_service.checkpointer.delete_thread(thread_id)
    directory = AUDIO_ROOT / hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:32]
    if directory.exists():
        shutil.rmtree(directory)
    for message in messages:
        session.delete(message)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/voice-messages/{message_id}/audio")
def get_audio(message_id: str, range: str | None = Header(default=None), session: Session = Depends(get_session)):
    message = session.get(ConversationMessage, message_id)
    if message is None or not message.audio_path:
        raise HTTPException(status_code=404, detail="Audio message not found")
    path = (AUDIO_ROOT / message.audio_path).resolve()
    root = AUDIO_ROOT.resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")
    content = path.read_bytes()
    headers = {"Accept-Ranges": "bytes"}
    if range and range.startswith("bytes="):
        try:
            start_text, end_text = range[6:].split("-", 1)
            start = int(start_text)
            end = int(end_text) if end_text else len(content) - 1
            if start < 0 or end < start or end >= len(content):
                raise ValueError
        except ValueError as exc:
            raise HTTPException(status_code=416, detail="Invalid audio range") from exc
        headers["Content-Range"] = f"bytes {start}-{end}/{len(content)}"
        return Response(
            content[start : end + 1],
            status_code=206,
            media_type=message.audio_content_type,
            headers=headers,
        )
    return Response(content, media_type=message.audio_content_type, headers=headers)


@router.post(
    "/api/voice-messages/{message_id}/transcribe",
    response_model=VoiceMessageTurnResponse,
)
async def transcribe_message(
    message_id: str,
    request: Request,
    x_personalive_request: str = Header(default=""),
    session: Session = Depends(get_session),
):
    if x_personalive_request != "web":
        raise HTTPException(status_code=403, detail="Missing same-origin request header")
    message = session.get(ConversationMessage, message_id)
    if message is None or message.kind != "audio" or not message.audio_path:
        raise HTTPException(status_code=404, detail="Audio message not found")
    path = (AUDIO_ROOT / message.audio_path).resolve()
    if AUDIO_ROOT.resolve() not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file not found")

    message.status = "transcribing"
    message.error_message = None
    session.commit()
    try:
        provider = request.app.state.asr_provider_factory(Settings.load())
        transcript = await provider.transcribe(path.name, message.audio_content_type or "audio/webm", path.read_bytes())
    except ASREmptyResultError as exc:
        message.status = "failed"
        message.error_message = "No speech was recognized"
        session.commit()
        raise HTTPException(status_code=422, detail=message.error_message) from exc
    except ASRUpstreamError as exc:
        message.status = "failed"
        message.error_message = "Local speech transcription failed"
        session.commit()
        raise HTTPException(status_code=502, detail=message.error_message) from exc

    message.transcript = transcript
    message.content = transcript
    message.status = "completed"
    context = context_for(request, session, message.persona_id, message.conversation_id)
    result = await asyncio.to_thread(request.app.state.agent_service.query, transcript, context)
    assistant = ConversationMessage(
        workspace_id=message.workspace_id,
        persona_id=message.persona_id,
        conversation_id=message.conversation_id,
        role="assistant",
        kind="text",
        content=result.answer,
        status="completed",
    )
    session.add(assistant)
    session.commit()
    session.refresh(message)
    return {"message": message_response(message), "turn": response_for(result)}
