import asyncio
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect

from voice.asr.streaming import SAMPLE_RATE, StreamSession

MANAGED_MODEL = Path(__file__).resolve().parents[2] / "models" / "Qwen3-ASR-0.6B"
MODEL_ID = os.getenv("PERSONALIVE_ASR_MODEL") or str(MANAGED_MODEL)
INFER_LOCK = asyncio.Lock()
MAX_PCM_BYTES_PER_MESSAGE = 1 << 20


def _default_model_provider() -> Any:
    import torch
    from qwen_asr import Qwen3ASRModel

    if not Path(MODEL_ID).joinpath("config.json").is_file():
        raise RuntimeError(
            f"本地 ASR 模型不存在：{MODEL_ID}。"
            f"请在「设置 → 本地语音识别」中完成安装，模型将下载到 {MANAGED_MODEL}。"
        )
    return Qwen3ASRModel.from_pretrained(
        MODEL_ID,
        dtype=torch.bfloat16,
        device_map="cuda:0",
        max_inference_batch_size=1,
        max_new_tokens=256,
    )


def create_worker_app(model_provider: Callable[[], Any] | None = None) -> FastAPI:
    """Build the ASR worker app; model_provider is injectable for tests."""

    provider = model_provider or _default_model_provider
    app = FastAPI(title="PersonaLive Local ASR")
    state: dict[str, Any] = {"model": None}

    @app.on_event("startup")
    def load_model() -> None:
        state["model"] = provider()

    def transcribe_sync(audio: Any, language: str | None) -> tuple[str, str]:
        # The HTTP path passes a file path (str); the streaming path passes a
        # bare PCM array, which qwen_asr only accepts as (array, sample_rate).
        payload = audio if isinstance(audio, str) else (audio, SAMPLE_RATE)
        result = state["model"].transcribe(audio=payload, language=language)[0]
        return str(result.language), str(result.text).strip()

    async def infer(audio: Any, language: str | None = None) -> tuple[str, str]:
        # GPU access is serialized and moved off the event loop.
        async with INFER_LOCK:
            return await asyncio.to_thread(transcribe_sync, audio, language)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ready" if state["model"] is not None else "loading"}

    @app.post("/transcribe")
    async def transcribe(request: Request, x_audio_filename: str = Header(default="recording.webm")) -> dict[str, str]:
        audio = await request.body()
        if not audio:
            raise HTTPException(status_code=422, detail="Audio is empty")
        suffix = Path(x_audio_filename).suffix or ".webm"
        path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
                temporary.write(audio)
                path = temporary.name
            _, text = await infer(path, None)
            return {"text": text}
        finally:
            if path:
                Path(path).unlink(missing_ok=True)

    @app.websocket("/ws/transcribe")
    async def stream_transcribe(websocket: WebSocket) -> None:
        """Streaming transcription protocol.

        Client -> server (text JSON):
            {"type": "start"}            begin a new utterance session
            {"type": "partial"}          transcribe what has been buffered
            {"type": "final"}            transcribe and close the utterance
            {"type": "cancel"}           discard the utterance
            {"type": "ping"}
        Client -> server (binary): mono PCM s16le at 16 kHz.
        Server -> client (text JSON):
            ready / started / partial / final / cancelled / pong / error
        """

        await websocket.accept()
        session: StreamSession | None = None
        started = False
        try:
            await websocket.send_json(
                {"type": "ready", "sample_rate": SAMPLE_RATE, "format": "pcm_s16le"}
            )
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
                if message["type"] != "websocket.receive":
                    continue
                if "bytes" in message:
                    payload = message["bytes"]
                    if not started or session is None:
                        await websocket.send_json(
                            {"type": "error", "code": "not_started", "message": "Send a start event first"}
                        )
                        continue
                    if len(payload) > MAX_PCM_BYTES_PER_MESSAGE:
                        await websocket.send_json(
                            {"type": "error", "code": "chunk_too_large", "message": "Audio chunk exceeds 1 MiB"}
                        )
                        continue
                    if len(payload) % 2:
                        payload = payload[:-1]
                    if payload:
                        pcm = np.frombuffer(payload, dtype=np.int16).astype(np.float32) / 32768.0
                        try:
                            await session.feed(pcm)
                        except ValueError:
                            session = None
                            started = False
                            await websocket.send_json(
                                {"type": "error", "code": "utterance_too_long", "message": "Utterance exceeded max duration"}
                            )
                    continue

                try:
                    command = json.loads(message["text"])
                except (json.JSONDecodeError, KeyError):
                    await websocket.send_json(
                        {"type": "error", "code": "invalid_command", "message": "Commands must be JSON objects"}
                    )
                    continue
                action = command.get("type")
                if action == "start":
                    language = command.get("language")
                    session = StreamSession(infer=lambda pcm: infer(pcm, language))
                    started = True
                    await websocket.send_json({"type": "started"})
                elif action in ("partial", "final") and started and session is not None:
                    try:
                        language, text = await session.transcribe()
                    except Exception as exc:
                        await websocket.send_json(
                            {"type": "error", "code": "transcribe_failed", "message": str(exc)}
                        )
                        continue
                    await websocket.send_json({"type": action, "text": text, "language": language})
                    if action == "final":
                        session = None
                        started = False
                elif action == "cancel":
                    if session is not None:
                        session.cancel()
                    session = None
                    started = False
                    await websocket.send_json({"type": "cancelled"})
                elif action == "ping":
                    await websocket.send_json({"type": "pong"})
                else:
                    await websocket.send_json(
                        {"type": "error", "code": "unknown_command", "message": f"Unsupported command: {action}"}
                    )
        except WebSocketDisconnect:
            pass
        finally:
            if session is not None:
                session.cancel()

    return app


app = create_worker_app()


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=17004)
