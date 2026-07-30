from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from voice.asr.base import ASREmptyResultError, ASRProvider, ASRUpstreamError

if TYPE_CHECKING:
    from settings import Settings


WORKER_URL = "http://127.0.0.1:8765"
QWEN_BUNDLE_PYTHON = Path(r"D:\Qwen3_ASR\WPy64-312101\python\python.exe")
QWEN_BUNDLE_BIN = Path(r"D:\Qwen3_ASR\bin")
_managers: dict[Path, "LocalASRManager"] = {}


class LocalASRManager:
    def __init__(self, project_root: Path, worker_url: str = WORKER_URL) -> None:
        self.project_root = project_root
        self.worker_url = worker_url
        self.runtime_dir = project_root / ".asr-venv"
        self.requirements = project_root / "voice" / "asr" / "requirements-local.txt"
        self.process: subprocess.Popen | None = None
        self._lock = asyncio.Lock()

    @property
    def python(self) -> Path:
        configured = os.getenv("PERSONALIVE_ASR_PYTHON", "").strip()
        if configured and Path(configured).is_file():
            return Path(configured)
        if QWEN_BUNDLE_PYTHON.is_file():
            return QWEN_BUNDLE_PYTHON
        return self.runtime_python

    @property
    def runtime_python(self) -> Path:
        return self.runtime_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    async def ensure_ready(self) -> None:
        async with self._lock:
            if await self._healthy():
                return
            await asyncio.to_thread(self._ensure_runtime)
            env = os.environ.copy()
            env["HF_HOME"] = str(self.project_root / "data" / "models")
            if QWEN_BUNDLE_BIN.is_dir():
                env["PATH"] = f"{QWEN_BUNDLE_BIN}{os.pathsep}{env.get('PATH', '')}"
            self.process = subprocess.Popen(
                [str(self.python), "-B", "-m", "voice.asr.worker_server"],
                cwd=self.project_root,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            for _ in range(180):
                if await self._healthy():
                    return
                await asyncio.sleep(1)
            raise ASRUpstreamError("Local ASR worker did not become ready")

    async def _healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                return (await client.get(f"{self.worker_url}/health")).is_success
        except httpx.HTTPError:
            return False

    def _ensure_runtime(self) -> None:
        if self.python != self.runtime_python:
            return
        marker = self.runtime_dir / ".ready"
        if marker.is_file() and self.runtime_python.is_file():
            return
        if not self.runtime_python.is_file():
            subprocess.run([sys.executable, "-m", "venv", str(self.runtime_dir)], check=True)
        subprocess.run(
            [str(self.runtime_python), "-m", "pip", "install", "-r", str(self.requirements)],
            cwd=self.project_root,
            check=True,
        )
        marker.write_text("ready\n", encoding="ascii")


class LocalQwenASR(ASRProvider):
    def __init__(
        self,
        manager: LocalASRManager | None = None,
        client: httpx.AsyncClient | None = None,
        worker_url: str = WORKER_URL,
    ) -> None:
        self.manager = manager
        self.client = client
        self.worker_url = worker_url

    async def transcribe(self, filename: str, content_type: str, audio: bytes) -> str:
        if self.manager is not None:
            await self.manager.ensure_ready()
        headers = {"Content-Type": content_type, "X-Audio-Filename": Path(filename).name}
        try:
            if self.client is not None:
                response = await self.client.post("/transcribe", content=audio, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=120, trust_env=False) as client:
                    response = await client.post(f"{self.worker_url}/transcribe", content=audio, headers=headers)
        except httpx.HTTPError as exc:
            raise ASRUpstreamError("Local ASR worker request failed") from exc
        if not response.is_success:
            raise ASRUpstreamError(f"Local ASR worker returned HTTP {response.status_code}")
        try:
            text = response.json().get("text", "").strip()
        except (ValueError, AttributeError) as exc:
            raise ASRUpstreamError("Local ASR worker returned an invalid response") from exc
        if not text:
            raise ASREmptyResultError("No speech was recognized")
        return text


def build_asr_provider(settings: Settings) -> LocalQwenASR:
    root = settings.project_root.resolve()
    manager = _managers.setdefault(root, LocalASRManager(root))
    return LocalQwenASR(manager=manager)
