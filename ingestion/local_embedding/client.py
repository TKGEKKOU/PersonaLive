"""主应用侧的 LangChain Embeddings 适配器。"""

import json
import os
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

from langchain_core.embeddings import Embeddings

from ingestion.local_embedding.resources import LocalEmbeddingResourceManager


_EMBEDDING_INSTANCES: list["ManagedLocalEmbeddings"] = []


def shutdown_embedding_workers() -> None:
    """终止所有已启动的本地 Embedding 推理子进程（退出清理用）。"""

    for instance in list(_EMBEDDING_INSTANCES):
        try:
            instance.close()
        except Exception:
            pass


def worker_environment() -> dict[str, str]:
    """为独立推理进程固定 UTF-8，避免 Windows GBK 破坏中文 JSON。"""
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    return environment


class ManagedLocalEmbeddings(Embeddings):
    """本地 Embedding 的 LangChain 适配器。

    设计选择：推理放在独立子进程（worker.py）里，通过 stdin/stdout 按行传 JSON。
    原因：1) 模型权重常驻子进程内存，批量请求不重复加载；2) 推理崩溃只影响子进程，
    主服务可自动重启恢复；3) 子进程可单独指定 Python 运行时/设备（CPU/GPU），
    与主进程依赖隔离。
    """

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
        # 拉起独立推理进程，并等待它输出第一行 JSON 握手（{"ok": true}），
        # 握手成功才算启动完成；启动失败时读 stderr 尾部作为错误详情。
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
                # 进程未启动或已退出（崩溃）时自动拉起新进程，对调用方透明。
                process = self._start()
            assert process.stdin is not None and process.stdout is not None
            # 行协议：主进程写一条 JSON 请求，读一条 JSON 响应；加锁保证串行，
            # 因为一个子进程同一时刻只处理一条请求。
            process.stdin.write(json.dumps({"operation": operation, "texts": texts}, ensure_ascii=False) + "\n")
            process.stdin.flush()
            line = process.stdout.readline()
            if not line:
                # 读取失败说明子进程已经退出，先关掉旧句柄，下次调用会重新拉起。
                self.close()
                raise RuntimeError("本地 Embedding 工作进程意外退出")
            result = json.loads(line)
            if not result.get("ok"):
                raise RuntimeError(str(result.get("error") or "本地 Embedding 推理失败"))
            return result["vectors"]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # LangChain Embeddings 接口：批量嵌入待检索文档（入库时调用）。
        return self._request("embed_documents", texts)

    def embed_query(self, text: str) -> list[float]:
        # LangChain Embeddings 接口：单条查询向量（检索时调用）。
        return self._request("embed_query", [text])[0]

    def close(self) -> None:
        process = self._process
        self._process = None
        if process and process.poll() is None:
            process.terminate()


@lru_cache(maxsize=4)
def get_managed_embeddings(project_root: str, model_id: str, device: str) -> ManagedLocalEmbeddings:
    instance = ManagedLocalEmbeddings(Path(project_root), model_id, device)
    _EMBEDDING_INSTANCES.append(instance)
    return instance
