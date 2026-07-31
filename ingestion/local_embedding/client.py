"""主应用侧的 LangChain Embeddings 适配器。"""

import json
import os
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

from langchain_core.embeddings import Embeddings

from ingestion.local_embedding.resources import LocalEmbeddingResourceManager


def worker_environment() -> dict[str, str]:
    """为独立推理进程固定 UTF-8，避免 Windows GBK 破坏中文 JSON。"""
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


class ManagedLocalEmbeddings(Embeddings):
    def __init__(self, project_root: Path, model_id: str, device: str) -> None:
        self.resources = LocalEmbeddingResourceManager(project_root)
        self.model_id = model_id
        self.device = device
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _start(self) -> subprocess.Popen:
        directory = self.resources.model_directory(self.model_id)
        if not (directory / "config.json").is_file():
            raise RuntimeError("本地 Embedding 模型尚未下载，请先在设置页完成安装")
        if not self.resources.runtime_python.is_file():
            raise RuntimeError("本地 Embedding 运行环境尚未安装")
        process = subprocess.Popen(
            [str(self.resources.runtime_python), str(self.resources.worker_script), str(directory), self.device],
            cwd=self.resources.project_root,
            env=worker_environment(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        ready_line = process.stdout.readline() if process.stdout else ""
        try:
            ready = json.loads(ready_line)
        except json.JSONDecodeError as exc:
            detail = process.stderr.read() if process.stderr and process.poll() is not None else ready_line
            process.terminate()
            raise RuntimeError(f"本地 Embedding 工作进程启动失败：{detail[-2000:]}") from exc
        if not ready.get("ok"):
            process.terminate()
            raise RuntimeError(str(ready.get("error") or "本地 Embedding 工作进程启动失败"))
        self._process = process
        return process

    def _request(self, operation: str, texts: list[str]) -> list[list[float]]:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                process = self._start()
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps({"operation": operation, "texts": texts}, ensure_ascii=False) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
            if not line:
                self.close()
                raise RuntimeError("本地 Embedding 工作进程意外退出")
            result = json.loads(line)
            if not result.get("ok"):
                raise RuntimeError(str(result.get("error") or "本地 Embedding 推理失败"))
            return result["vectors"]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._request("embed_documents", texts)

    def embed_query(self, text: str) -> list[float]:
        return self._request("embed_query", [text])[0]

    def close(self) -> None:
        process = self._process
        self._process = None
        if process and process.poll() is None:
            process.terminate()


@lru_cache(maxsize=4)
def get_managed_embeddings(project_root: str, model_id: str, device: str) -> ManagedLocalEmbeddings:
    return ManagedLocalEmbeddings(Path(project_root), model_id, device)
