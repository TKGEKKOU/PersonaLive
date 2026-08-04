"""受管本地 Embedding 模型的安装、状态和目录操作。"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from settings import DEFAULT_LOCAL_EMBEDDING_MODEL, Settings
from voice.resource_directory import open_resource_directory


MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


class EmbeddingInstallCancelled(RuntimeError):
    pass


def validate_model_id(model_id: str) -> str:
    value = model_id.strip()
    if not MODEL_ID_PATTERN.fullmatch(value) or ".." in value.split("/"):
        raise ValueError("模型 ID 只能包含字母、数字、点、短横线、下划线和单个路径分隔符")
    return value


class LocalEmbeddingResourceManager:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.models_root = self.project_root / "models"
        self.local_settings_path = self.project_root / "data" / "local_settings.json"
        self.runtime_dir = self.project_root / "runtime" / "embedding"
        self.requirements = self.project_root / "ingestion" / "local_embedding" / "requirements-local.txt"
        self.worker_script = self.project_root / "ingestion" / "local_embedding" / "worker.py"
        self._installing = False
        self._cancel_requested = threading.Event()
        self._process: subprocess.Popen | None = None
        self._error = ""
        self._phase = "idle"
        self._current_file = ""
        self._started_at: float | None = None
        self._lock = threading.Lock()

    @property
    def runtime_python(self) -> Path:
        return self.runtime_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def model_directory(self, model_id: str) -> Path:
        safe_id = validate_model_id(model_id)
        directory = (self.models_root / safe_id.replace("/", "--")).resolve()
        if directory.parent != self.models_root.resolve():
            raise ValueError("模型目录超出项目 models 范围")
        return directory

    @staticmethod
    def _metadata_path(directory: Path) -> Path:
        return directory / ".yumeno-model.json"

    def _read_metadata(self, directory: Path) -> dict:
        path = self._metadata_path(directory)
        try:
            value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _active(self) -> tuple[Settings, Path, dict]:
        settings = Settings.load(self.project_root)
        model_id = settings.embedding_model or DEFAULT_LOCAL_EMBEDDING_MODEL
        directory = self.model_directory(model_id)
        return settings, directory, self._read_metadata(directory)

    def status(self) -> dict:
        settings, directory, metadata = self._active()
        elapsed = time.monotonic() - self._started_at if self._started_at else 0
        installed = (directory / "config.json").is_file()
        ready = installed if settings.embedding_provider == "managed_local" else bool(
            settings.embedding_api_key and settings.embedding_base_url and settings.embedding_model
        )
        return {
            "provider": settings.embedding_provider,
            "model_id": settings.embedding_model or DEFAULT_LOCAL_EMBEDDING_MODEL,
            "source": settings.embedding_model_source,
            "device": settings.embedding_device,
            "actual_device": str(metadata.get("actual_device") or ""),
            "dimensions": int(metadata.get("dimensions") or settings.embedding_dimensions),
            "installed": installed,
            "ready": ready,
            "installing": self._installing,
            "cancelling": self._installing and self._cancel_requested.is_set(),
            "phase": self._phase,
            "current_file": self._current_file,
            "downloaded_bytes": 0,
            "total_bytes": 0,
            "progress_percent": None,
            "download_speed_bytes": 0,
            "eta_seconds": None,
            "elapsed_seconds": round(elapsed),
            "error": self._error,
            "model_dir": str(directory if directory.is_dir() else ""),
            "models_root": str(self.models_root),
        }

    def configure(self, model_id: str, source: str, device: str) -> dict:
        validate_model_id(model_id)
        if source not in {"modelscope", "huggingface"} or device not in {"auto", "cuda", "cpu"}:
            raise ValueError("本地 Embedding 配置无效")
        try:
            values = json.loads(self.local_settings_path.read_text(encoding="utf-8")) if self.local_settings_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            values = {}
        values.update({
            "embedding_provider": "managed_local",
            "embedding_model": model_id,
            "embedding_model_source": source,
            "embedding_device": device,
            "embedding_send_dimensions": False,
        })
        self.local_settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.local_settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.local_settings_path)
        return self.status()

    def start_install(self, model_id: str, source: str, device: str) -> bool:
        validate_model_id(model_id)
        if source not in {"modelscope", "huggingface"}:
            raise ValueError("不支持的模型下载源")
        if device not in {"auto", "cuda", "cpu"}:
            raise ValueError("不支持的运行设备")
        self.configure(model_id, source, device)
        with self._lock:
            if self._installing:
                return False
            self._installing = True
            self._cancel_requested.clear()
            self._error = ""
            self._phase = "preparing"
            self._current_file = model_id
            self._started_at = time.monotonic()
        threading.Thread(
            target=self._install,
            args=(model_id, source, device),
            daemon=True,
            name="embedding-install",
        ).start()
        return True

    def cancel_install(self) -> bool:
        with self._lock:
            if not self._installing:
                return False
            self._cancel_requested.set()
            self._phase = "cancelling"
            process = self._process
        if process and process.poll() is None:
            process.terminate()
        return True

    def _run(self, command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        if self._cancel_requested.is_set():
            raise EmbeddingInstallCancelled()
        process = subprocess.Popen(
            command,
            cwd=self.project_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        with self._lock:
            self._process = process
        stdout, stderr = process.communicate()
        with self._lock:
            self._process = None
        if self._cancel_requested.is_set():
            raise EmbeddingInstallCancelled()
        if process.returncode:
            raise RuntimeError((stderr or stdout or "子进程执行失败")[-3000:])
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def _install_runtime(self) -> None:
        self._phase = "runtime"
        if not self.runtime_python.is_file():
            subprocess.run([sys.executable, "-m", "venv", str(self.runtime_dir)], check=True)
        marker = self.runtime_dir / ".requirements-ready"
        if marker.is_file():
            return
        pypi = os.getenv("YUMENO_PYPI_INDEX", "https://mirrors.aliyun.com/pypi/simple/")
        pytorch = os.getenv("YUMENO_PYTORCH_INDEX", "https://mirrors.aliyun.com/pytorch-wheels/cu128/")
        pip_command = [
            str(self.runtime_python), "-m", "pip", "install", "--timeout", "60", "--retries", "2",
            "--index-url", pypi, "--extra-index-url", pytorch, "-r", str(self.requirements),
        ]
        try:
            self._run(pip_command)
        except EmbeddingInstallCancelled:
            raise
        except RuntimeError as domestic_error:
            # 国内镜像并不总是同步 CUDA Wheel；仅 PyTorch 源回退，其他依赖继续走国内 PyPI。
            fallback = os.getenv("YUMENO_PYTORCH_FALLBACK_INDEX", "https://download.pytorch.org/whl/cu128")
            pip_command[pip_command.index(pytorch)] = fallback
            self._current_file = "PyTorch CUDA 12.8（官方备用源）"
            try:
                self._run(pip_command)
            except EmbeddingInstallCancelled:
                raise
            except RuntimeError as fallback_error:
                raise RuntimeError(
                    f"国内 PyTorch 镜像缺少所需版本，官方备用源也安装失败：{fallback_error}"
                ) from domestic_error
        marker.write_text("ready\n", encoding="ascii")

    def _install(self, model_id: str, source: str, device: str) -> None:
        directory = self.model_directory(model_id)
        try:
            self._install_runtime()
            self._phase = "model"
            self._current_file = model_id
            directory.mkdir(parents=True, exist_ok=True)
            if source == "modelscope":
                code = "from modelscope import snapshot_download; snapshot_download(%r, local_dir=%r)" % (model_id, str(directory))
            else:
                code = "from huggingface_hub import snapshot_download; snapshot_download(repo_id=%r, local_dir=%r)" % (model_id, str(directory))
            env = os.environ.copy()
            env["MODELSCOPE_CACHE"] = str(self.project_root / "runtime" / "modelscope-cache")
            env["HF_HOME"] = str(self.project_root / "runtime" / "huggingface-cache")
            self._run([str(self.runtime_python), "-c", code], env=env)
            self._phase = "loading"
            probe = self._run([str(self.runtime_python), str(self.worker_script), "--probe", str(directory), device])
            result = json.loads(probe.stdout.strip().splitlines()[-1])
            if not result.get("ok"):
                raise RuntimeError(str(result.get("error") or "模型维度探测失败"))
            self._metadata_path(directory).write_text(
                json.dumps({
                    "model_id": model_id,
                    "source": source,
                    "dimensions": result["dimensions"],
                    "actual_device": result["actual_device"],
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 维度由真实模型输出探测，避免用户手填后与 Milvus Collection 不一致。
            try:
                values = json.loads(self.local_settings_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                values = {}
            values["embedding_dimensions"] = int(result["dimensions"])
            temporary = self.local_settings_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, self.local_settings_path)
            self._phase = "complete"
        except EmbeddingInstallCancelled:
            self._error = ""
            self._phase = "idle"
        except (OSError, RuntimeError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            self._error = str(exc)
            self._phase = "error"
        finally:
            with self._lock:
                self._installing = False
                self._process = None
            self._cancel_requested.clear()
            self._started_at = None

    def remove_model(self) -> dict:
        if self._installing:
            raise RuntimeError("请先取消正在进行的下载")
        _, directory, _ = self._active()
        if directory.is_dir():
            shutil.rmtree(directory)
        self._error = ""
        self._phase = "idle"
        return self.status()

    def open_model_directory(self) -> dict:
        _, directory, _ = self._active()
        target = directory if directory.is_dir() else self.models_root
        return {**self.status(), "opened_directory": open_resource_directory(target)}
