from __future__ import annotations

import asyncio
import json

import numpy as np
import websockets

WORKER_WS_URL = "ws://127.0.0.1:17004/ws/transcribe"


class WorkerStreamError(RuntimeError):
    pass


class WorkerStreamClient:
    """WebSocket client to the local ASR worker's streaming endpoint.

    One instance is created per utterance by the voice stream router; the
    worker accumulates whatever audio is fed to it and transcribes it on
    demand, so the main app only needs to control cadence and endpointing.
    """

    def __init__(self, url: str = WORKER_WS_URL, timeout: float = 60.0) -> None:
        self._url = url
        self._timeout = timeout
        self._ws: websockets.ClientConnection | None = None

    async def connect(self, language: str | None = None) -> None:
        self._ws = await websockets.connect(self._url, ping_interval=20, ping_timeout=20)
        ready = await self._recv()
        if ready.get("type") != "ready":
            raise WorkerStreamError(f"Unexpected worker greeting: {ready}")
        command: dict = {"type": "start"}
        if language:
            command["language"] = language
        await self._send_text(command)
        started = await self._recv()
        if started.get("type") != "started":
            raise WorkerStreamError(f"Unexpected worker start response: {started}")

    async def feed(self, pcm: np.ndarray) -> None:
        samples = np.asarray(pcm)
        if samples.size == 0:
            return
        if samples.dtype != np.int16:
            samples = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
        await self._send_binary(samples.tobytes())

    async def partial(self) -> str:
        await self._send_text({"type": "partial"})
        event = await self._recv()
        if event.get("type") != "partial":
            raise WorkerStreamError(f"Expected partial, got {event}")
        return event.get("text", "")

    async def final(self) -> tuple[str, str]:
        await self._send_text({"type": "final"})
        event = await self._recv()
        if event.get("type") != "final":
            raise WorkerStreamError(f"Expected final, got {event}")
        return event.get("language", ""), event.get("text", "")

    async def cancel(self) -> None:
        try:
            if self._ws is not None:
                await self._ws.send(json.dumps({"type": "cancel"}))
        except Exception:
            pass
        finally:
            await self.close()

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _send_text(self, payload: dict) -> None:
        await self._send_raw(json.dumps(payload))

    async def _send_binary(self, payload: bytes) -> None:
        await self._send_raw(payload)

    async def _send_raw(self, payload: object) -> None:
        if self._ws is None:
            raise WorkerStreamError("Worker connection is closed")
        try:
            await self._ws.send(payload)
        except Exception as exc:
            raise WorkerStreamError(f"Worker connection failed: {exc}") from exc

    async def _recv(self) -> dict:
        if self._ws is None:
            raise WorkerStreamError("Worker connection is closed")
        raw = await asyncio.wait_for(self._ws.recv(), timeout=self._timeout)
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            raise WorkerStreamError(f"Invalid worker event: {raw!r}") from exc
