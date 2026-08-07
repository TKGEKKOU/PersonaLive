import asyncio
import hashlib
import io
import audioop
import base64
import json
import wave
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Path as FastAPIPath,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.database import get_session
from app.models import ConversationMessage, Persona, VoiceAsset
from app.routers.messages import message_response
from app.routers.personas import local_persona_or_404
from app.routers.settings import require_local
from app.routers.voice_assets import asset_reference_prompt
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
MAX_REFERENCE_SECONDS = 30


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


def persona_voice_asset(persona: Persona, session: Session) -> VoiceAsset | None:
    """Trained GPT-SoVITS voice bound to the persona, if any."""

    asset_id = ((persona.profile_json or {}).get("tts") or {}).get("voice_asset_id")
    if not asset_id:
        return None
    asset = session.get(VoiceAsset, asset_id)
    if (
        asset is not None
        and asset.status == "ready"
        and asset.gpt_weights_path
        and asset.sovits_weights_path
    ):
        return asset
    return None


def persona_voice_lang(persona: Persona) -> str:
    return str(((persona.profile_json or {}).get("tts") or {}).get("voice_lang") or "zh")


def _synthesize_part_to_bytes(
    worker,
    text: str,
    reference: Path | None,
    asset: VoiceAsset | None = None,
    adapter=None,
    text_lang: str = "zh",
) -> bytes:
    """Synthesize one text chunk with the persona's bound engine."""

    if asset is not None and adapter is not None:
        refer_audio, prompt_text = asset_reference_prompt(asset)
        return adapter.synthesize(
            text,
            text_lang=text_lang,
            gpt_weights=asset.gpt_weights_path,
            sovits_weights=asset.sovits_weights_path,
            refer_audio=refer_audio,
            prompt_text=prompt_text,
            prompt_lang=text_lang,
        )
    return worker.synthesize_to_bytes(text, reference)


def _synthesize_text(request: Request, session: Session, persona: Persona, text: str, output: Path) -> None:
    """Route synthesis to GPT-SoVITS (bound trained voice) or Lunar."""

    asset = persona_voice_asset(persona, session)
    if asset is None:
        raise RuntimeError("该角色未绑定训练音色（GPT-SoVITS），请到角色编辑页选择音色")
    refer_audio, prompt_text = asset_reference_prompt(asset)
    lang = persona_voice_lang(persona)
    audio = request.app.state.gpt_sovits.synthesize(
        text,
        text_lang=lang,
        gpt_weights=asset.gpt_weights_path,
        sovits_weights=asset.sovits_weights_path,
        refer_audio=refer_audio,
        prompt_text=prompt_text,
        prompt_lang=lang,
    )
    output.write_bytes(audio)


def _persist_incremental_audio(
    worker,
    directory: Path,
    session_factory,
    persona_id: str,
    conversation_id: str,
    texts: list[str],
    parts: list[bytes],
) -> dict:
    """合并增量合成的分段并持久化一条完整音频消息。"""
    content = "".join(texts)
    merged = worker.merge_wavs(parts)
    output = directory / f"{uuid4()}.wav"
    output.write_bytes(merged)
    db = session_factory()
    try:
        message = ConversationMessage(
            workspace_id=LOCAL_WORKSPACE_ID,
            persona_id=persona_id,
            conversation_id=conversation_id,
            role="assistant",
            kind="audio",
            content=content,
            audio_path=str(output.relative_to(directory.parent)),
            audio_content_type="audio/wav",
            status="completed",
        )
        db.add(message)
        db.commit()
        db.refresh(message)
        return message_response(message)
    finally:
        db.close()


class TTSConfigUpdate(BaseModel):
    enabled: bool | None = None
    use_gpu: bool | None = None
    model_variant: str | None = None
    engine: str | None = None


class TTSSynthesisRequest(BaseModel):
    text: str


def protected(request: Request, header: str) -> None:
    require_local(request)
    if header != "web":
        raise HTTPException(status_code=403, detail="Missing same-origin request header")


@router.get("/status")
def get_status(request: Request):
    require_local(request)
    data = request.app.state.tts_resources.status()
    gpt = request.app.state.gpt_sovits.status()
    if gpt.get("installed"):
        # GPT-SoVITS 是当前唯一可用的对话语音引擎；聊天页面的可用性以它的
        # 安装状态为准（API 服务会在首次合成时按需启动）。
        data["engine"] = "gpt_sovits"
        data["ready"] = bool(data.get("enabled", True) and gpt.get("installed"))
        data["gpt_sovits"] = gpt
    return data


@router.patch("/config")
def update_config(payload: TTSConfigUpdate, request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    values = payload.model_dump(exclude_unset=True)
    current = request.app.state.tts_resources.config()
    if "use_gpu" in values and values["use_gpu"] != current["use_gpu"]:
        request.app.state.tts_worker.stop_service()
        request.app.state.tts_worker.use_gpu = values["use_gpu"]
    if "model_variant" in values and values["model_variant"] != current["model_variant"]:
        # The engine loads the model at process start; restart so the next
        # synthesis uses the newly activated variant files.
        request.app.state.tts_worker.stop_service()
    if "engine" in values and values["engine"] != current["engine"]:
        # Switching the active TTS engine: stop the Lunar service when the
        # GPT-SoVITS engine takes over.
        request.app.state.tts_worker.stop_service()
    return request.app.state.tts_resources.configure(**values)


@router.post("/install", status_code=status.HTTP_202_ACCEPTED)
def install(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    if not request.app.state.tts_resources.start_install():
        raise HTTPException(status_code=409, detail="TTS 安装已在进行中")
    # Stop any running engine so the freshly downloaded variant is picked up
    # on the next synthesis instead of the previously loaded model.
    request.app.state.tts_worker.stop_service()
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
        _synthesize_text(request, session, persona, text, output)
    except (TTSGenerationError, RuntimeError, OSError, ValueError) as exc:
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
                audio = worker.synthesize_to_bytes(segment, reference)
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


@router.websocket("/personas/{persona_id}/conversations/{conversation_id}/synthesize/ws")
async def synthesize_ws(
    websocket: WebSocket,
    persona_id: str,
    conversation_id: str = FastAPIPath(min_length=1, max_length=255),
) -> None:
    """增量语音合成（WebSocket）：客户端逐句发送 {"type":"text","text":"..."}，
    服务端随到随合成并以 {"type":"segment","audio":base64} 回推；
    收到 {"type":"done"} 后合并、持久化一条音频消息并回 {"type":"done","message":...}。

    选择 WebSocket 而非流式 HTTP 请求体：uvicorn 的 h11 实现不支持
    chunked 请求体，浏览器流式上传到 uvicorn 会丢失数据。
    """
    await websocket.accept()
    try:
        with websocket.app.state.session_factory() as db:
            persona = local_persona_or_404(db, persona_id)
    except HTTPException as exc:
        await websocket.send_text(
            json.dumps({"type": "error", "message": str(exc.detail)}, ensure_ascii=False)
        )
        await websocket.close()
        return
    directory = AUDIO_ROOT / hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:32]
    directory.mkdir(parents=True, exist_ok=True)
    worker = websocket.app.state.tts_factory()
    with websocket.app.state.session_factory() as db:
        asset = persona_voice_asset(persona, db)
    adapter = websocket.app.state.gpt_sovits if asset is not None else None
    if asset is not None:
        if not websocket.app.state.gpt_sovits.status().get("installed"):
            await websocket.send_text(
                json.dumps({"type": "error", "message": "GPT-SoVITS 未安装，无法合成该音色"}, ensure_ascii=False)
            )
            await websocket.close()
            return
    else:
        await websocket.send_text(
            json.dumps({"type": "error", "message": "该角色未绑定训练音色（GPT-SoVITS），请到角色编辑页选择音色"}, ensure_ascii=False)
        )
        await websocket.close()
        return
    reference = reference_path(persona)
    parts: list[bytes] = []
    texts: list[str] = []
    try:
        while True:
            try:
                message = await websocket.receive_json()
            except WebSocketDisconnect:
                # 客户端主动中断（停止播放/离开页面）：丢弃未完成内容，不持久化。
                return
            if not isinstance(message, dict):
                await websocket.send_text(
                    json.dumps({"type": "error", "message": "invalid message"}, ensure_ascii=False)
                )
                continue
            msg_type = message.get("type")
            if msg_type == "text":
                line = str(message.get("text") or "").strip()
                if not line:
                    continue
                texts.append(line)
                try:
                    audio = await asyncio.to_thread(
                        _synthesize_part_to_bytes,
                        worker,
                        line,
                        reference,
                        asset,
                        adapter,
                        persona_voice_lang(persona),
                    )
                except Exception as exc:
                    await websocket.send_text(
                        json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False)
                    )
                    return
                parts.append(audio)
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "segment",
                            "index": len(parts) - 1,
                            "text": line,
                            "audio": base64.b64encode(audio).decode("ascii"),
                        },
                        ensure_ascii=False,
                    )
                )
            elif msg_type == "done":
                break
            else:
                await websocket.send_text(
                    json.dumps(
                        {"type": "error", "message": f"unexpected message type: {msg_type}"},
                        ensure_ascii=False,
                    )
                )
        if not texts:
            await websocket.send_text(
                json.dumps({"type": "error", "message": "TTS 文本为空"}, ensure_ascii=False)
            )
            return
        payload_message = _persist_incremental_audio(
            worker,
            directory,
            websocket.app.state.session_factory,
            persona_id,
            conversation_id,
            texts,
            parts,
        )
        await websocket.send_text(
            json.dumps(
                {"type": "done", "message": payload_message},
                ensure_ascii=False,
                default=_json_default,
            )
        )
    except TTSGenerationError as exc:
        await websocket.send_text(
            json.dumps(
                {"type": "error", "message": str(exc)}, ensure_ascii=False, default=_json_default
            )
        )
    except Exception as exc:  # noqa: BLE001 - 流式连接需要把错误推给前端
        await websocket.send_text(
            json.dumps(
                {"type": "error", "message": str(exc)}, ensure_ascii=False, default=_json_default
            )
        )
    finally:
        try:
            await websocket.close()
        except RuntimeError:  # pragma: no cover - 连接已断开时忽略关闭错误
            pass
