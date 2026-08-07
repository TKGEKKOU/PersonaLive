"""Lunar TTS resource management (the only bundled engine).

YUMENO ships one lightweight TTS engine: the Lunar C++ runtime (bundled in
``runtime/tts``) plus Qwen3-TTS-0.6B GGUF weights downloaded from
ModelScope. Voice cloning runs in x-vector-only mode (speaker embedding,
no reference transcript required).
"""

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from voice.resource_directory import open_resource_directory


# Model variants: same engine (qwen3-tts.cpp), different GGUF precision.
# The engine scans models/Qwen3-TTS for .gguf files, so only the active
# variant's files live at the top level; inactive files are archived under
# models/Qwen3-TTS/.variants/<id>/ and moved back when selected.
TTS_VARIANTS = {
    "f16": {
        "label": "F16 标准版",
        "description": "约 2.2 GB，质量优先（默认）",
        "talker": "qwen3-tts-0.6b-f16.gguf",
        "tokenizer": "qwen3-tts-tokenizer-f16.gguf",
        "talker_url": "https://modelscope.cn/models/qwqpotato/qwen3-tts-gguf/resolve/master/qwen3-tts-0.6b-f16.gguf",
        "tokenizer_url": "https://modelscope.cn/models/qwqpotato/qwen3-tts-gguf/resolve/master/qwen3-tts-tokenizer-f16.gguf",
        "source": "ModelScope",
    },
}

DEFAULT_TTS_VARIANT = "f16"

LUNAR_MODEL_BASE = TTS_VARIANTS[DEFAULT_TTS_VARIANT]["talker_url"].rsplit("/", 1)[0]


class TTSInstallCancelled(RuntimeError):
    pass


class _InstallState:
    """Install progress for the Lunar engine."""

    def __init__(self) -> None:
        self.installing = False
        self.cancel_requested = threading.Event()
        self.phase = "idle"
        self.current_file = ""
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.error = ""
        self.started_at: float | None = None
        self.source = ""

    def set_progress(self, phase: str, current_file: str = "", downloaded: int = 0, total: int = 0) -> None:
        self.phase = phase
        self.current_file = current_file
        self.downloaded_bytes = downloaded
        self.total_bytes = total

    def snapshot(self) -> dict:
        elapsed = time.monotonic() - self.started_at if self.started_at else 0
        speed = self.downloaded_bytes / elapsed if elapsed > 0 and self.downloaded_bytes else 0
        remaining = self.total_bytes - self.downloaded_bytes
        return {
            "installing": self.installing,
            "cancelling": self.installing and self.cancel_requested.is_set(),
            "phase": self.phase,
            "current_file": self.current_file,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "progress_percent": round(self.downloaded_bytes * 100 / self.total_bytes) if self.total_bytes else None,
            "download_speed_bytes": round(speed),
            "eta_seconds": round(remaining / speed) if speed > 0 and remaining > 0 else 0 if self.total_bytes and remaining <= 0 else None,
            "elapsed_seconds": round(elapsed),
            "error": self.error,
            "source": self.source,
        }


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
        self.variants_dir = self.model_dir / ".variants"
        self.state = _InstallState()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------

    @staticmethod
    def _valid_variant(variant: str | None) -> str:
        value = variant or DEFAULT_TTS_VARIANT
        if value not in TTS_VARIANTS:
            raise ValueError(f"unknown TTS model variant: {value!r}")
        return value

    def _active_paths(self, variant: str) -> tuple[Path, Path]:
        spec = TTS_VARIANTS[variant]
        return self.model_dir / spec["talker"], self.model_dir / spec["tokenizer"]

    def _stored_paths(self, variant: str) -> tuple[Path, Path]:
        spec = TTS_VARIANTS[variant]
        directory = self.variants_dir / variant
        return directory / spec["talker"], directory / spec["tokenizer"]

    def _variant_installed(self, variant: str) -> bool:
        active = self._active_paths(variant)
        stored = self._stored_paths(variant)
        return all(p.is_file() for p in active) or all(p.is_file() for p in stored)

    def _activate(self, variant: str) -> None:
        """Move the selected variant's files to the top level so the engine
        (which scans models/Qwen3-TTS) always sees exactly one talker and
        one tokenizer."""
        variant = self._valid_variant(variant)
        active = self._active_paths(variant)
        if all(p.is_file() for p in active):
            return
        for other, spec in TTS_VARIANTS.items():
            if other == variant:
                continue
            for filename in (spec["talker"], spec["tokenizer"]):
                source = self.model_dir / filename
                if source.is_file():
                    directory = self.variants_dir / other
                    directory.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(directory / filename))
        for filename in (TTS_VARIANTS[variant]["talker"], TTS_VARIANTS[variant]["tokenizer"]):
            stored = self.variants_dir / variant / filename
            if stored.is_file():
                shutil.move(str(stored), str(self.model_dir / filename))

    def config(self) -> dict:
        defaults = {
            "enabled": True,
            "use_gpu": True,
            "model_variant": DEFAULT_TTS_VARIANT,
            "engine": "lunar",
        }
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
            "model_variant": self._valid_variant(values.get("model_variant")),
            "engine": "gpt_sovits" if values.get("engine") == "gpt_sovits" else "lunar",
        }

    def configure(
        self,
        enabled: bool | None = None,
        use_gpu: bool | None = None,
        model_variant: str | None = None,
        engine: str | None = None,
    ) -> dict:
        values = self.config()
        if enabled is not None:
            values["enabled"] = enabled
        if use_gpu is not None:
            values["use_gpu"] = use_gpu
        if model_variant is not None:
            values["model_variant"] = self._valid_variant(model_variant)
            self._activate(values["model_variant"])
        if engine is not None:
            values["engine"] = "gpt_sovits" if engine == "gpt_sovits" else "lunar"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.config_path)
        return self.status()

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def installed(self) -> bool:
        variant = self.config()["model_variant"]
        return (
            self.runtime_path.is_file()
            and self.runtime_dll_path.is_file()
            and self._variant_installed(variant)
        )

    def status(self) -> dict:
        values = self.config()
        ok = self.installed()
        snapshot = self.state.snapshot()
        variant = values["model_variant"]
        return {
            **values,
            "installed": ok,
            "runtime_bundled": self.runtime_path.is_file() and self.runtime_dll_path.is_file(),
            "managed_installed": self.model_dir.exists(),
            "ready": bool(values["enabled"] and ok),
            "variant_installed": self._variant_installed(variant),
            "variants": [
                {
                    "id": vid,
                    "label": spec["label"],
                    "description": spec["description"],
                    "installed": self._variant_installed(vid),
                    "active": vid == variant,
                    "source": spec["source"],
                }
                for vid, spec in TTS_VARIANTS.items()
            ],
            "installing": snapshot["installing"],
            "cancelling": snapshot["cancelling"],
            "phase": snapshot["phase"],
            "current_file": snapshot["current_file"],
            "downloaded_bytes": snapshot["downloaded_bytes"],
            "total_bytes": snapshot["total_bytes"],
            "progress_percent": snapshot["progress_percent"],
            "download_speed_bytes": snapshot["download_speed_bytes"],
            "eta_seconds": snapshot["eta_seconds"],
            "elapsed_seconds": snapshot["elapsed_seconds"],
            "error": snapshot["error"],
            "source": snapshot["source"],
            "download_size": f"{TTS_VARIANTS[variant]['description']}，运行库已内置",
            "source_default": "modelscope",
            "model_dir": str(self.model_dir) if self.model_dir.is_dir() else "",
        }

    # ------------------------------------------------------------------
    # install
    # ------------------------------------------------------------------

    def start_install(self) -> bool:
        with self._lock:
            if self.state.installing:
                return False
            self.state.installing = True
            self.state.cancel_requested.clear()
            self.state.error = ""
            self.state.started_at = time.monotonic()
            self.state.set_progress("preparing", "", 0, 0)
        threading.Thread(target=self._install, daemon=True, name="tts-install").start()
        return True

    def cancel_install(self) -> bool:
        if not self.state.installing:
            return False
        self.state.cancel_requested.set()
        self.state.phase = "cancelling"
        return True

    def _install(self) -> None:
        try:
            self._install_lunar(self.state)
            self.state.set_progress("complete", "", 0, 0)
        except TTSInstallCancelled:
            self.state.error = ""
            self.state.set_progress("idle", "", 0, 0)
        except (OSError, RuntimeError, urllib.error.URLError) as exc:
            if self.state.cancel_requested.is_set():
                self.state.error = ""
                self.state.set_progress("idle", "", 0, 0)
            else:
                self.state.error = str(exc)
                self.state.phase = "error"
        finally:
            self.state.installing = False
            self.state.cancel_requested.clear()
            self.state.started_at = None

    def _install_lunar(self, state: _InstallState) -> None:
        if not self.runtime_path.is_file() or not self.runtime_dll_path.is_file():
            raise RuntimeError("当前开发目录缺少内置 Lunar TTS 运行库，请使用完整 Windows 发布包")
        variant = self.config()["model_variant"]
        spec = TTS_VARIANTS[variant]
        self._activate(variant)
        for filename, url, destination in (
            (spec["talker"], spec["talker_url"], self.model_dir / spec["talker"]),
            (spec["tokenizer"], spec["tokenizer_url"], self.model_dir / spec["tokenizer"]),
        ):
            if not destination.is_file():
                self._download(state, url, destination, "model")

    def _download(self, state: _InstallState, url: str, destination: Path, phase: str = "model") -> None:
        """Download with byte-level progress, direct connection (bypassing any
        injected proxy), resume support and automatic retries."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        if state.cancel_requested.is_set():
            raise TTSInstallCancelled()
        state.source = urlsplit(url).hostname or "下载源"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        attempt = 0
        while True:
            attempt += 1
            if state.cancel_requested.is_set():
                raise TTSInstallCancelled()
            resume = partial.stat().st_size if partial.is_file() else 0
            headers = {"User-Agent": "YUMENO"}
            if resume > 0:
                headers["Range"] = f"bytes={resume}-"
            request = urllib.request.Request(url, headers=headers)
            try:
                with opener.open(request, timeout=60) as response, partial.open("ab" if resume else "wb") as target:
                    total = resume + int(response.headers.get("Content-Length") or 0)
                    downloaded = resume
                    state.set_progress(phase, destination.name, downloaded, total)
                    while chunk := response.read(1024 * 1024):
                        if state.cancel_requested.is_set():
                            raise TTSInstallCancelled()
                        target.write(chunk)
                        downloaded += len(chunk)
                        state.set_progress(phase, destination.name, downloaded, total)
                os.replace(partial, destination)
                return
            except TTSInstallCancelled:
                # Keep the partial file so a later attempt resumes where it left off.
                raise
            except (OSError, urllib.error.URLError) as exc:
                if attempt > 2:
                    raise RuntimeError(f"下载 {destination.name} 失败（{state.source}）：{exc}") from exc
                time.sleep(2)

    # ------------------------------------------------------------------
    # remove / open
    # ------------------------------------------------------------------

    def remove_models(self) -> dict:
        if self.state.installing:
            raise RuntimeError("请先取消正在进行的下载")
        if self.model_dir.exists():
            shutil.rmtree(self.model_dir)
        self.state.error = ""
        self.state.set_progress("idle", "", 0, 0)
        return self.status()

    def open_model_directory(self) -> dict:
        return {**self.status(), "opened_directory": open_resource_directory(self.model_dir)}
