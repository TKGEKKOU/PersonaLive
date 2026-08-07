"""On-demand installation of the HT-Demucs vocals ONNX model."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
import urllib.request
from pathlib import Path

from voice.resource_directory import open_resource_directory

# fp16 weights variant: 165,612,636 bytes on disk, fp32 compute at runtime.
MODEL_FILENAME = "htdemucs_ft_vocals_fp16weights.onnx"
MODEL_SIZE = 165612636
MODEL_SHA256 = "0cbe651f535415c9d26a7bb614f7d322dd5a080fa0298f2e50f478030a994dce"
MODEL_URLS = (
    f"https://hf-mirror.com/StemSplitio/htdemucs-ft-vocals-onnx/resolve/main/{MODEL_FILENAME}",
    f"https://huggingface.co/StemSplitio/htdemucs-ft-vocals-onnx/resolve/main/{MODEL_FILENAME}",
)


class SeparatorResourceManager:
    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.data_dir = self.project_root / "data" / "separator"
        self.config_path = self.data_dir / "config.json"
        self.model_dir = self.project_root / "models" / "separator"
        self.model_path = self.model_dir / MODEL_FILENAME
        self._installing = False
        self._cancel_requested = threading.Event()
        self._phase = "idle"
        self._error = ""
        self._started_at: float | None = None
        self._downloaded_bytes = 0
        self._total_bytes = 0
        self._lock = threading.Lock()

    def config(self) -> dict:
        defaults = {"enabled": True}
        if not self.config_path.is_file():
            return defaults
        try:
            values = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return defaults
        return {**defaults, **{key: values.get(key, default) for key, default in defaults.items()}}

    def configure(self, **changes) -> dict:
        values = self.config()
        for key in values:
            if key in changes and changes[key] is not None:
                values[key] = changes[key]
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.config_path)
        return self.status()

    def installed(self) -> bool:
        return self.model_path.is_file() and self.model_path.stat().st_size == MODEL_SIZE

    def status(self) -> dict:
        values = self.config()
        return {
            **values,
            "installed": self.installed(),
            "ready": bool(values["enabled"] and self.installed()),
            "installing": self._installing,
            "cancelling": self._installing and self._cancel_requested.is_set(),
            "phase": self._phase,
            "progress_percent": (
                round(self._downloaded_bytes * 100 / self._total_bytes) if self._total_bytes else None
            ),
            "downloaded_bytes": self._downloaded_bytes,
            "total_bytes": self._total_bytes,
            "elapsed_seconds": round(time.monotonic() - self._started_at) if self._started_at else 0,
            "source": "huggingface / hf-mirror",
            "error": self._error,
            "resolved_model": str(self.model_path),
            "model_size": "约 165 MB",
        }

    def start_install(self) -> bool:
        with self._lock:
            if self._installing:
                return False
            self._installing = True
            self._cancel_requested.clear()
            self._error = ""
            self._phase = "preparing"
            self._downloaded_bytes = 0
            self._total_bytes = MODEL_SIZE
            self._started_at = time.monotonic()
        threading.Thread(target=self._install, daemon=True, name="separator-install").start()
        return True

    def cancel_install(self) -> bool:
        with self._lock:
            if not self._installing:
                return False
            self._cancel_requested.set()
            self._phase = "cancelling"
        return True

    def _download(self, url: str, target: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "YUMENO/0.1"})
        chunk_size = 1024 * 1024
        with urllib.request.urlopen(request, timeout=60) as response:
            content_length = int(response.headers.get("Content-Length") or 0)
            with self._lock:
                self._total_bytes = content_length or MODEL_SIZE
            with open(target, "wb") as output:
                while True:
                    if self._cancel_requested.is_set():
                        raise RuntimeError("下载已取消")
                    block = response.read(chunk_size)
                    if not block:
                        break
                    output.write(block)
                    with self._lock:
                        self._downloaded_bytes += len(block)

    def _install(self) -> None:
        try:
            self.model_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.model_dir / f"{MODEL_FILENAME}.part"
            if temporary.exists():
                temporary.unlink()
            last_error: Exception | None = None
            for url in MODEL_URLS:
                if self._cancel_requested.is_set():
                    return
                self._phase = "model"
                try:
                    self._download(url, temporary)
                    break
                except (OSError, urllib.error.URLError, RuntimeError) as exc:
                    last_error = exc
                    temporary.unlink(missing_ok=True)
                    with self._lock:
                        self._downloaded_bytes = 0
            if not temporary.is_file():
                raise RuntimeError(f"模型下载失败：{last_error}")
            if temporary.stat().st_size != MODEL_SIZE:
                raise RuntimeError(f"模型文件不完整（{temporary.stat().st_size} / {MODEL_SIZE} 字节）")
            digest = hashlib.sha256()
            with open(temporary, "rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != MODEL_SHA256:
                raise RuntimeError("模型校验失败，文件可能已损坏，请重新安装")
            self._phase = "complete"
            os.replace(temporary, self.model_path)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            if self._cancel_requested.is_set():
                self._phase = "idle"
                self._error = ""
            else:
                self._phase = "error"
                self._error = str(exc)
        finally:
            self._installing = False
            self._cancel_requested.clear()
            self._started_at = None
            self._downloaded_bytes = 0
            self._total_bytes = 0

    def remove_models(self) -> dict:
        if self._installing:
            return self.status()
        if self.model_dir.exists():
            shutil.rmtree(self.model_dir)
        self._error = ""
        return self.status()

    def open_model_directory(self) -> dict:
        return {**self.status(), "opened_directory": open_resource_directory(self.model_dir)}
