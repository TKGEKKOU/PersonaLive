import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from voice.resource_directory import open_resource_directory


MODEL_BASE = "https://modelscope.cn/models/qwqpotato/qwen3-tts-gguf/resolve/master"


class TTSInstallCancelled(RuntimeError):
    pass


class TTSResourceManager:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.data_dir = self.project_root / "data" / "tts"
        self.config_path = self.data_dir / "config.json"
        self.runtime_dir = self.project_root / "runtime" / "tts"
        self.runtime_path = self.runtime_dir / ("Qwen3_TTS_Lunar.exe" if os.name == "nt" else "Qwen3_TTS_Lunar")
        self.runtime_dll_path = self.runtime_dir / ("qwen3tts.dll" if os.name == "nt" else "libqwen3tts.so")
        self.model_dir = self.project_root / "models" / "Qwen3-TTS"
        self.model_path = self.model_dir / "qwen3-tts-0.6b-f16.gguf"
        self.tokenizer_path = self.model_dir / "qwen3-tts-tokenizer-f16.gguf"
        self._installing = False
        self._cancel_requested = threading.Event()
        self._download_started_at: float | None = None
        self._error = ""
        self._phase = "idle"
        self._current_file = ""
        self._downloaded_bytes = 0
        self._total_bytes = 0
        self._lock = threading.Lock()

    def config(self) -> dict:
        defaults = {"enabled": True, "use_gpu": True}
        if not self.config_path.is_file():
            return defaults
        try:
            values = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return defaults
        return {
            **defaults,
            "enabled": bool(values.get("enabled", True)),
            "use_gpu": bool(values.get("use_gpu", True)),
        }

    def configure(self, enabled: bool | None = None, use_gpu: bool | None = None) -> dict:
        values = self.config()
        if enabled is not None:
            values["enabled"] = enabled
        if use_gpu is not None:
            values["use_gpu"] = use_gpu
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.config_path)
        return self.status()

    def status(self) -> dict:
        values = self.config()
        runtime_bundled = self.runtime_path.is_file() and self.runtime_dll_path.is_file()
        installed = runtime_bundled and self.model_path.is_file() and self.tokenizer_path.is_file()
        elapsed = time.monotonic() - self._download_started_at if self._download_started_at else 0
        speed = self._downloaded_bytes / elapsed if elapsed > 0 and self._downloaded_bytes else 0
        remaining = self._total_bytes - self._downloaded_bytes
        return {
            **values,
            "installed": installed,
            "runtime_bundled": runtime_bundled,
            "managed_installed": self.runtime_dir.exists() or self.model_dir.exists(),
            "ready": bool(values["enabled"] and installed),
            "installing": self._installing,
            "cancelling": self._installing and self._cancel_requested.is_set(),
            "error": self._error,
            "phase": self._phase,
            "current_file": self._current_file,
            "downloaded_bytes": self._downloaded_bytes,
            "total_bytes": self._total_bytes,
            "progress_percent": round(self._downloaded_bytes * 100 / self._total_bytes) if self._total_bytes else None,
            "download_speed_bytes": round(speed),
            "eta_seconds": round(remaining / speed) if speed > 0 and remaining > 0 else 0 if self._total_bytes and remaining <= 0 else None,
            "download_size": "约 3 GB",
            "source": "modelscope",
            "runtime": str(self.runtime_path if runtime_bundled else ""),
            "model_dir": str(self.model_dir if self.model_dir.is_dir() else ""),
        }

    def start_install(self) -> bool:
        with self._lock:
            if self._installing:
                return False
            self._installing = True
            self._cancel_requested.clear()
            self._download_started_at = None
            self._error = ""
            self._set_progress("preparing", "", 0, 0)
        threading.Thread(target=self._install, daemon=True, name="tts-install").start()
        return True

    def cancel_install(self) -> bool:
        with self._lock:
            if not self._installing:
                return False
            self._cancel_requested.set()
            self._phase = "cancelling"
            return True

    def _set_progress(self, phase: str, current_file: str, downloaded: int, total: int) -> None:
        self._phase = phase
        self._current_file = current_file
        self._downloaded_bytes = downloaded
        self._total_bytes = total

    def _download(self, url: str, destination: Path, phase: str = "download") -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        if self._cancel_requested.is_set():
            raise TTSInstallCancelled()
        request = urllib.request.Request(url, headers={"User-Agent": "YUMENO"})
        self._set_progress(phase, destination.name, 0, 0)
        self._download_started_at = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as target:
                total = int(response.headers.get("Content-Length") or 0)
                downloaded = 0
                while chunk := response.read(1024 * 1024):
                    if self._cancel_requested.is_set():
                        raise TTSInstallCancelled()
                    target.write(chunk)
                    downloaded += len(chunk)
                    self._set_progress(phase, destination.name, downloaded, total)
        except TTSInstallCancelled:
            partial.unlink(missing_ok=True)
            raise
        except (OSError, urllib.error.URLError) as exc:
            source = urlsplit(url).hostname or "下载源"
            label = "C++ 运行库" if phase == "runtime" else "模型"
            raise RuntimeError(f"下载{label}失败（{source}）：{exc}") from exc
        os.replace(partial, destination)

    def _install(self) -> None:
        try:
            if not self.runtime_path.is_file() or not self.runtime_dll_path.is_file():
                raise RuntimeError("当前开发目录缺少内置 Lunar TTS 运行库，请使用完整 Windows 发布包")
            model_base = os.getenv("YUMENO_TTS_MODEL_BASE", MODEL_BASE).rstrip("/")
            for filename, destination in (
                (self.model_path.name, self.model_path),
                (self.tokenizer_path.name, self.tokenizer_path),
            ):
                if not destination.is_file():
                    self._download(f"{model_base}/{filename}", destination, "model")
            self._set_progress("complete", "", 0, 0)
        except TTSInstallCancelled:
            self._error = ""
            self._set_progress("idle", "", 0, 0)
        except (OSError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as exc:
            self._error = str(exc)
            self._phase = "error"
        finally:
            self._installing = False
            self._cancel_requested.clear()
            self._download_started_at = None

    def _extract_runtime(self, archive: Path) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        root = self.runtime_dir.resolve()
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                destination = (root / member.filename).resolve()
                if destination != root and root not in destination.parents:
                    raise RuntimeError("TTS 运行库包含不安全的文件路径")
            package.extractall(root)

    def remove_models(self) -> dict:
        if self._installing:
            raise RuntimeError("请先取消正在进行的下载")
        if self.model_dir.exists():
            shutil.rmtree(self.model_dir)
        self._error = ""
        self._set_progress("idle", "", 0, 0)
        return self.status()

    def open_model_directory(self) -> dict:
        return {**self.status(), "opened_directory": open_resource_directory(self.model_dir)}
