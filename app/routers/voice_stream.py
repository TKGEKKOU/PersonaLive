from __future__ import annotations

import json
import time

import numpy as np
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from app.routers.settings import require_local
from settings import Settings
from voice.vad import VAD, build_vad, detect_vad_provider

router = APIRouter(prefix="/api/voice/stream", tags=["voice-stream"])

SAMPLE_RATE = 16000
VOICE_LANGUAGE = "Chinese"
MAX_STREAM_SECONDS = 90
MAX_UTTERANCE_SECONDS = 30
MIN_PARTIAL_SECONDS = 0.7
PARTIAL_EVERY_SECONDS = 1.2
LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


@router.get("/status")
def stream_status(request: Request) -> dict:
    require_local(request)
    return {
        "vad_provider": detect_vad_provider(),
        "sample_rate": SAMPLE_RATE,
        "format": "pcm_s16le",
        "partial_every_seconds": PARTIAL_EVERY_SECONDS,
    }


class _StreamState:
    def __init__(self) -> None:
        self.buffer = np.zeros(0, dtype=np.float32)
        self.buffer_offset = 0
        self.samples_seen = 0
        self.utterance_start: int | None = None
        self.speech_active = False
        self.worker = None
        self.worker_next = 0
        self.partial_next = 0
        self.utterance_started_at = 0.0
        self.last_vad_state = ""

    def reset(self) -> None:
        self.buffer = np.zeros(0, dtype=np.float32)
        self.buffer_offset = 0
        self.samples_seen = 0
        self.utterance_start = None
        self.speech_active = False
        self.worker = None
        self.worker_next = 0
        self.partial_next = 0
        self.utterance_started_at = 0.0
        self.last_vad_state = ""


@router.websocket("/ws")
async def voice_stream(websocket: WebSocket) -> None:
    host = websocket.client.host if websocket.client else ""
    if host not in LOCAL_HOSTS:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    vad: VAD = websocket.app.state.vad_factory()
    state = _StreamState()
    try:
        await websocket.send_json(
            {
                "type": "session.ready",
                "sample_rate": SAMPLE_RATE,
                "format": "pcm_s16le",
                "vad": detect_vad_provider(),
            }
        )
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message["type"] != "websocket.receive":
                continue
            if "bytes" in message:
                await _on_audio(websocket, vad, state, message["bytes"])
            elif "text" in message:
                await _on_command(websocket, vad, state, message["text"])
    except WebSocketDisconnect:
        pass
    finally:
        await _cancel_utterance(state)


async def _on_audio(websocket: WebSocket, vad: VAD, state: _StreamState, payload: bytes) -> None:
    if not payload:
        return
    if len(payload) % 2:
        payload = payload[:-1]
    if not payload:
        return
    pcm = np.frombuffer(payload, dtype=np.int16).astype(np.float32) / 32768.0
    state.buffer = np.concatenate([state.buffer, pcm])
    max_samples = MAX_STREAM_SECONDS * SAMPLE_RATE
    overflow = state.buffer.shape[0] - max_samples
    if overflow > 0:
        state.buffer = state.buffer[overflow:]
        state.buffer_offset += overflow
    state.samples_seen += pcm.shape[0]

    for event in vad.process(pcm):
        if event.sample_index < state.buffer_offset:
            continue
        if event.kind == "speech_start":
            await _begin_utterance(websocket, state, event.sample_index)
        else:
            await _finish_utterance(websocket, state, event.sample_index)

    if state.speech_active and state.worker is not None:
        await _forward_and_partial(websocket, state)
        if time.monotonic() - state.utterance_started_at > MAX_UTTERANCE_SECONDS:
            await _finish_utterance(websocket, state, state.samples_seen)


async def _begin_utterance(websocket: WebSocket, state: _StreamState, start_index: int) -> None:
    await _cancel_utterance(state)
    worker = websocket.app.state.asr_stream_client_factory()
    try:
        await worker.connect(language=VOICE_LANGUAGE)
    except Exception as exc:
        state.worker = None
        await websocket.send_json(
            {"type": "error", "code": "worker_unavailable", "message": f"语音识别服务不可用：{exc}"}
        )
        return
    state.worker = worker
    state.utterance_start = start_index
    state.speech_active = True
    state.worker_next = start_index
    state.partial_next = start_index + int(MIN_PARTIAL_SECONDS * SAMPLE_RATE)
    state.utterance_started_at = time.monotonic()
    await _forward_audio(state)
    await _send_vad(websocket, state, "speaking")


async def _finish_utterance(websocket: WebSocket, state: _StreamState, stop_index: int) -> None:
    if state.worker is not None:
        await _forward_audio(state)
    worker, state.worker = state.worker, None
    state.speech_active = False
    duration_ms = 0
    if state.utterance_start is not None:
        duration_ms = int((stop_index - state.utterance_start) * 1000 / SAMPLE_RATE)
    state.utterance_start = None
    await _send_vad(websocket, state, "idle")
    if worker is None:
        return
    try:
        language, text = await worker.final()
    except Exception:
        text = ""
        language = ""
    finally:
        await worker.close()
    if text.strip():
        await websocket.send_json(
            {
                "type": "final",
                "text": text,
                "language": language,
                "duration_ms": duration_ms,
            }
        )
    else:
        await websocket.send_json(
            {"type": "error", "code": "empty", "message": "没有识别到语音"}
        )


async def _forward_audio(state: _StreamState) -> None:
    if state.worker is None or state.worker_next >= state.samples_seen:
        return
    index = state.worker_next - state.buffer_offset
    if index < 0:
        index = 0
        state.worker_next = state.buffer_offset
    try:
        await state.worker.feed(state.buffer[index:])
    except Exception:
        # The worker connection dropped mid-utterance; discard this utterance
        # instead of letting the error kill the voice stream socket.
        await _cancel_utterance(state)
        return
    state.worker_next = state.samples_seen


async def _forward_and_partial(websocket: WebSocket, state: _StreamState) -> None:
    await _forward_audio(state)
    if state.worker is None or state.samples_seen < state.partial_next:
        return
    try:
        text = await state.worker.partial()
    except Exception:
        text = ""
    state.partial_next = state.samples_seen + int(PARTIAL_EVERY_SECONDS * SAMPLE_RATE)
    if text.strip():
        await websocket.send_json({"type": "partial", "text": text})


async def _cancel_utterance(state: _StreamState) -> None:
    worker, state.worker = state.worker, None
    state.speech_active = False
    state.utterance_start = None
    if worker is not None:
        try:
            await worker.cancel()
        except Exception:
            pass


async def _send_vad(websocket: WebSocket, state: _StreamState, value: str) -> None:
    if state.last_vad_state == value:
        return
    state.last_vad_state = value
    await websocket.send_json({"type": "vad", "state": value})


async def _on_command(websocket: WebSocket, vad: VAD, state: _StreamState, raw: str) -> None:
    try:
        command = json.loads(raw)
    except json.JSONDecodeError:
        await websocket.send_json(
            {"type": "error", "code": "invalid_command", "message": "Commands must be JSON objects"}
        )
        return
    action = command.get("type")
    if action == "ping":
        await websocket.send_json({"type": "pong"})
    elif action == "start":
        await _cancel_utterance(state)
        try:
            provider = websocket.app.state.asr_provider_factory(Settings.load())
            manager = getattr(provider, "manager", None)
            if manager is not None:
                await manager.ensure_ready()
        except Exception as exc:
            await websocket.send_json(
                {"type": "error", "code": "asr_unavailable", "message": f"语音识别服务未就绪：{exc}"}
            )
            return
        vad.reset()
        state.reset()
        await websocket.send_json({"type": "started"})
    elif action == "cancel":
        await _cancel_utterance(state)
        await websocket.send_json({"type": "cancelled"})
    elif action == "finish":
        if state.speech_active:
            await _finish_utterance(websocket, state, state.samples_seen)
        else:
            await websocket.send_json(
                {"type": "error", "code": "not_speaking", "message": "当前没有正在识别的语音"}
            )
    else:
        await websocket.send_json(
            {"type": "error", "code": "unknown_command", "message": f"Unsupported command: {action}"}
        )
