import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path


BUNDLE_ROOT = Path(r"D:\Qwen3_ASR")
BUNDLE_PYTHON = BUNDLE_ROOT / "WPy64-312101" / "python" / "python.exe"
BUNDLE_MODEL = BUNDLE_ROOT / "models" / "Qwen" / "Qwen3-ASR-0.6B"
BUNDLE_FFMPEG = BUNDLE_ROOT / "bin" / "ffmpeg.exe"


@dataclass(frozen=True)
class ASRResources:
    python: Path | None
    model: Path | None
    ffmpeg: Path | None

    @property
    def ready(self) -> bool:
        return bool(
            self.python
            and self.python.is_file()
            and self.model
            and (self.model / "config.json").is_file()
            and self.ffmpeg
            and self.ffmpeg.is_file()
        )


class ASRResourceManager:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.data_dir = self.project_root / "data" / "asr"
        self.config_path = self.data_dir / "config.json"
        self.runtime_dir = self.project_root / "runtime" / "asr"
        self.managed_model = self.project_root / "models" / "Qwen3-ASR-0.6B"
        self.managed_ffmpeg = self.project_root / "runtime" / "ffmpeg" / "ffmpeg.exe"
        self.requirements = self.project_root / "voice" / "asr" / "requirements-local.txt"
        self._installing = False
        self._error = ""
        self._lock = threading.Lock()

    @property
    def runtime_python(self) -> Path:
        return self.runtime_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def config(self) -> dict:
        defaults = {"enabled": True, "python_path": "", "model_path": "", "ffmpeg_path": ""}
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

    @staticmethod
    def _file(configured: str, managed: Path, bundled: Path, system_name: str = "") -> Path | None:
        candidates = [Path(configured).expanduser() if configured else None, managed, bundled]
        if system_name:
            located = shutil.which(system_name)
            candidates.append(Path(located) if located else None)
        return next((path.resolve() for path in candidates if path and path.is_file()), None)

    @staticmethod
    def _model(configured: str, managed: Path, bundled: Path) -> Path | None:
        candidates = [Path(configured).expanduser() if configured else None, managed, bundled]
        return next((path.resolve() for path in candidates if path and (path / "config.json").is_file()), None)

    def resolve(self) -> ASRResources:
        values = self.config()
        return ASRResources(
            python=self._file(values["python_path"] or os.getenv("PERSONALIVE_ASR_PYTHON", ""), self.runtime_python, BUNDLE_PYTHON),
            model=self._model(values["model_path"] or os.getenv("PERSONALIVE_ASR_MODEL", ""), self.managed_model, BUNDLE_MODEL),
            ffmpeg=self._file(values["ffmpeg_path"] or os.getenv("PERSONALIVE_ASR_FFMPEG", ""), self.managed_ffmpeg, BUNDLE_FFMPEG, "ffmpeg"),
        )

    def status(self) -> dict:
        values = self.config()
        resources = self.resolve()
        return {
            **values,
            "installed": resources.ready,
            "managed_installed": self.runtime_dir.is_dir() or self.managed_model.is_dir() or self.managed_ffmpeg.is_file(),
            "ready": bool(values["enabled"] and resources.ready),
            "installing": self._installing,
            "error": self._error,
            "resolved_python": str(resources.python or ""),
            "resolved_model": str(resources.model or ""),
            "resolved_ffmpeg": str(resources.ffmpeg or ""),
            "download_size": "约 5-10 GB（含 CUDA PyTorch 与 1.88 GB 模型）",
        }

    def start_install(self) -> bool:
        with self._lock:
            if self._installing:
                return False
            self._installing = True
            self._error = ""
        threading.Thread(target=self._install, daemon=True, name="asr-install").start()
        return True

    def _install(self) -> None:
        try:
            if not self.runtime_python.is_file():
                subprocess.run([sys.executable, "-m", "venv", str(self.runtime_dir)], check=True)
            pypi_index = os.getenv("PERSONALIVE_PYPI_INDEX", "https://mirrors.aliyun.com/pypi/simple/")
            pytorch_index = os.getenv(
                "PERSONALIVE_PYTORCH_INDEX",
                "https://mirrors.aliyun.com/pytorch-wheels/cu128/",
            )
            subprocess.run(
                [
                    str(self.runtime_python),
                    "-m",
                    "pip",
                    "install",
                    "--index-url",
                    pypi_index,
                    "--extra-index-url",
                    pytorch_index,
                    "-r",
                    str(self.requirements),
                ],
                cwd=self.project_root,
                check=True,
            )
            model_id = os.getenv("PERSONALIVE_ASR_MODEL_ID", "Qwen/Qwen3-ASR-0.6B")
            script = (
                "from modelscope import snapshot_download; "
                f"snapshot_download({model_id!r}, local_dir={str(self.managed_model)!r})"
            )
            download_env = os.environ.copy()
            download_env["MODELSCOPE_CACHE"] = str(self.project_root / "runtime" / "modelscope-cache")
            subprocess.run(
                [str(self.runtime_python), "-c", script],
                cwd=self.project_root,
                env=download_env,
                check=True,
            )
            self.managed_ffmpeg.parent.mkdir(parents=True, exist_ok=True)
            ffmpeg_script = (
                "import shutil; from imageio_ffmpeg import get_ffmpeg_exe; "
                f"shutil.copy2(get_ffmpeg_exe(), {str(self.managed_ffmpeg)!r})"
            )
            subprocess.run([str(self.runtime_python), "-c", ffmpeg_script], cwd=self.project_root, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            self._error = str(exc)
        finally:
            self._installing = False

    def remove_managed(self) -> dict:
        for target in (self.runtime_dir, self.managed_model, self.managed_ffmpeg.parent):
            if target.exists():
                shutil.rmtree(target)
        self._error = ""
        return self.status()
