import atexit
import base64
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


class TTSGenerationError(RuntimeError):
    pass


class LocalTTS:
    def __init__(
        self,
        runtime_path: Path,
        model_dir: Path,
        port: int | None = None,
        use_gpu: bool = True,
        opener: Callable = urlopen,
        process_factory: Callable = subprocess.Popen,
        sleeper: Callable = time.sleep,
    ) -> None:
        self.runtime_path = Path(runtime_path)
        self.model_dir = Path(model_dir)
        if port is None:
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
        self.port = port
        self.use_gpu = use_gpu
        self.opener = opener
        self.process_factory = process_factory
        self.sleeper = sleeper
        self._process: subprocess.Popen | None = None
        self._using_gpu: bool | None = None
        self._process_lock = threading.Lock()
        atexit.register(self.stop_service)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _request(self, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base_url}{path}",
            data=body,
            headers={"Content-Type": "application/json"} if body else {},
            method="POST" if body else "GET",
        )
        with self.opener(request, timeout=300 if body else 2) as response:
            return json.loads(response.read().decode("utf-8"))

    def _is_ready(self) -> bool:
        try:
            return self._request("/health").get("status") == "ok"
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            return False

    def _ensure_ready(self) -> None:
        if self._is_ready():
            return
        if not self.runtime_path.is_file():
            raise TTSGenerationError("Lunar TTS 运行库不存在")
        project_root = self.model_dir.parents[1] if self.model_dir.parent.name == "models" else self.model_dir.parent
        with self._process_lock:
            if not self._is_ready():
                process = self._process
                if process is not None and process.poll() is None and self._using_gpu != self.use_gpu:
                    process.terminate()
                    self._process = None
                    process = None
                if process is None or process.poll() is not None:
                    environment = os.environ.copy()
                    environment["PERSONALIVE_TTS_USE_GPU"] = "1" if self.use_gpu else "0"
                    self._process = self.process_factory(
                        [str(self.runtime_path), "--basic-port", str(self.port), "--local-dir", str(project_root)],
                        cwd=str(self.runtime_path.parent),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=environment,
                    )
                    self._using_gpu = self.use_gpu
                for _ in range(50):
                    if self._is_ready():
                        return
                    self.sleeper(0.1)
        raise TTSGenerationError("Lunar TTS 服务启动超时")

    def synthesize(self, text: str, output: Path, reference_audio: Path | None = None) -> Path:
        if not text.strip():
            raise TTSGenerationError("合成文本为空")
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_ready()
        payload = {"text": text.strip(), "max_tokens": 160}
        if reference_audio:
            payload["ref_audio"] = str(reference_audio)
        try:
            response = self._request("/tts", payload)
            if not response.get("success"):
                raise TTSGenerationError(str(response.get("error") or "Lunar TTS 合成失败"))
            audio = base64.b64decode(str(response.get("audio") or ""), validate=True)
        except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
            raise TTSGenerationError(f"本地语音生成失败：{exc}") from exc
        if not audio:
            raise TTSGenerationError("Lunar TTS 没有返回音频")
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_bytes(audio)
        temporary.replace(output)
        return output

    def stop_service(self) -> None:
        with self._process_lock:
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
            self._process = None
            self._using_gpu = None
