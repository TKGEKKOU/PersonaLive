import json
import os
import shutil
import threading
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit


MODEL_BASE = "https://modelscope.cn/models/qwqpotato/qwen3-tts-gguf/resolve/master"


class TTSResourceManager:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.data_dir = self.project_root / "data" / "tts"
        self.config_path = self.data_dir / "config.json"
        self.runtime_dir = self.project_root / "runtime" / "tts"
        self.cli_path = self.runtime_dir / ("qwen3-tts-cli.exe" if os.name == "nt" else "qwen3-tts-cli")
        self.model_dir = self.project_root / "models" / "Qwen3-TTS"
        self.model_path = self.model_dir / "qwen3-tts-0.6b-f16.gguf"
        self.tokenizer_path = self.model_dir / "qwen3-tts-tokenizer-f16.gguf"
        self._installing = False
        self._error = ""
        self._phase = "idle"
        self._current_file = ""
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
        return {**defaults, "enabled": bool(values.get("enabled", True))}

    def configure(self, enabled: bool | None = None) -> dict:
        values = self.config()
        if enabled is not None:
            values["enabled"] = enabled
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.config_path)
        return self.status()

    def status(self) -> dict:
        values = self.config()
        installed = self.cli_path.is_file() and self.model_path.is_file() and self.tokenizer_path.is_file()
        return {
            **values,
            "installed": installed,
            "runtime_bundled": self.cli_path.is_file(),
            "managed_installed": self.runtime_dir.exists() or self.model_dir.exists(),
            "ready": bool(values["enabled"] and installed),
            "installing": self._installing,
            "error": self._error,
            "phase": self._phase,
            "current_file": self._current_file,
            "downloaded_bytes": self._downloaded_bytes,
            "total_bytes": self._total_bytes,
            "progress_percent": round(self._downloaded_bytes * 100 / self._total_bytes) if self._total_bytes else None,
            "download_size": "约 3 GB",
            "runtime": str(self.cli_path if self.cli_path.is_file() else ""),
            "model_dir": str(self.model_dir if self.model_dir.is_dir() else ""),
        }

    def start_install(self) -> bool:
        with self._lock:
            if self._installing:
                return False
            self._installing = True
            self._error = ""
            self._set_progress("preparing", "", 0, 0)
        threading.Thread(target=self._install, daemon=True, name="tts-install").start()
        return True

    def _set_progress(self, phase: str, current_file: str, downloaded: int, total: int) -> None:
        self._phase = phase
        self._current_file = current_file
        self._downloaded_bytes = downloaded
        self._total_bytes = total

    def _download(self, url: str, destination: Path, phase: str = "download") -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        request = urllib.request.Request(url, headers={"User-Agent": "PersonaLive"})
        self._set_progress(phase, destination.name, 0, 0)
        try:
            with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as target:
                total = int(response.headers.get("Content-Length") or 0)
                downloaded = 0
                while chunk := response.read(1024 * 1024):
                    target.write(chunk)
                    downloaded += len(chunk)
                    self._set_progress(phase, destination.name, downloaded, total)
        except (OSError, urllib.error.URLError) as exc:
            source = urlsplit(url).hostname or "下载源"
            label = "C++ 运行库" if phase == "runtime" else "模型"
            raise RuntimeError(f"下载{label}失败（{source}）：{exc}") from exc
        os.replace(partial, destination)

    def _install(self) -> None:
        try:
            if not self.cli_path.is_file():
                raise RuntimeError("当前开发目录缺少内置 TTS 运行库 qwen3-tts-cli.exe，请使用完整 Windows 发布包")
            model_base = os.getenv("PERSONALIVE_TTS_MODEL_BASE", MODEL_BASE).rstrip("/")
            for filename, destination in (
                (self.model_path.name, self.model_path),
                (self.tokenizer_path.name, self.tokenizer_path),
            ):
                if not destination.is_file():
                    self._download(f"{model_base}/{filename}", destination, "model")
            self._set_progress("complete", "", 0, 0)
        except (OSError, RuntimeError, urllib.error.URLError, zipfile.BadZipFile) as exc:
            self._error = str(exc)
            self._phase = "error"
        finally:
            self._installing = False

    def _extract_runtime(self, archive: Path) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        root = self.runtime_dir.resolve()
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                destination = (root / member.filename).resolve()
                if destination != root and root not in destination.parents:
                    raise RuntimeError("TTS 运行库包含不安全的文件路径")
            package.extractall(root)

    def remove_managed(self) -> dict:
        for target in (self.runtime_dir, self.model_dir):
            if target.exists():
                shutil.rmtree(target)
        self._error = ""
        self._set_progress("idle", "", 0, 0)
        return self.status()
