"""HTTP adapter around a GPT-SoVITS installation's API service.

The adapter launches ``api_v2.py`` (or ``api.py``) as a child process using
the installation's own Python runtime, then talks to it over HTTP. No
GPT-SoVITS source code is modified, and the engine stays replaceable.
"""

import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from voice.gpt_sovits.config import GPTSoVITSConfig, InstallationProbe


class GPTSoVITSNotInstalled(RuntimeError):
    pass


class GPTSoVITSUnavailable(RuntimeError):
    pass


class GPTSoVITSAdapter:
    """Launches and drives the GPT-SoVITS HTTP API service."""

    def __init__(
        self,
        config: GPTSoVITSConfig,
        project_root: Path,
        opener: Callable = urlopen,
        sleeper: Callable = time.sleep,
    ) -> None:
        self.config = config
        self.project_root = Path(project_root).resolve()
        self.opener = opener
        self.sleeper = sleeper
        self._process: subprocess.Popen | None = None
        self._job = None
        self._lock = threading.Lock()
        self._loaded_gpt: str | None = None
        self._loaded_sovits: str | None = None
        self._loaded_refer: str | None = None

    # ------------------------------------------------------------------
    # service lifecycle
    # ------------------------------------------------------------------

    def probe(self) -> InstallationProbe:
        return self.config.probe()

    @property
    def port(self) -> int:
        return self.config.values()["api_port"]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _request(self, path: str, params: dict | None = None, payload: dict | None = None):
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"} if body else {},
            method="POST" if payload is not None else "GET",
        )
        with self.opener(request, timeout=300) as response:
            return response.read()

    def is_alive(self) -> bool:
        if self._process is not None and self._process.poll() is not None:
            return False
        # 端口可达即视为存活：兼容由启动页或历史会话启动、未被本实例追踪的服务，
        # 避免重复拉起第二个实例。
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=2):
                pass
            return True
        except OSError:
            return False

    @staticmethod
    def _listener_pids(port: int) -> list[int]:
        """Return PIDs listening on 127.0.0.1:port (via netstat)."""

        try:
            output = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        pids: list[int] = []
        needle = f":{port}"
        for line in output.splitlines():
            if needle not in line or "LISTENING" not in line.upper():
                continue
            for part in reversed(line.split()):
                if part.isdigit():
                    pids.append(int(part))
                    break
        return pids

    def _kill_listener(self) -> None:
        """Terminate any process still serving the API port (orphans from
        crashed sessions or untracked launcher instances)."""

        for pid in self._listener_pids(self.port):
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                )
            except (OSError, subprocess.SubprocessError):
                pass

    def _attach_kill_on_close_job(self, process: subprocess.Popen) -> None:
        """Windows Job Object：父进程（无论正常退出还是被强杀）关闭时自动终止
        子进程，避免 api_v2.py 变成孤儿进程。失败时静默降级为普通子进程。"""

        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            JobObjectExtendedLimitInformation = 9

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_longlong),
                    ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD),
                ]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong),
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(
                job,
                JobObjectExtendedLimitInformation,
                ctypes.byref(info),
                ctypes.sizeof(info),
            ):
                kernel32.CloseHandle(job)
                return
            if not kernel32.AssignProcessToJobObject(job, int(process._handle)):
                kernel32.CloseHandle(job)
                return
            self._close_job()
            self._job = job
        except Exception:
            pass

    def _close_job(self) -> None:
        if self._job:
            try:
                import ctypes

                ctypes.WinDLL("kernel32").CloseHandle(self._job)
            except Exception:
                pass
            self._job = None

    def ensure_service(self) -> None:
        """Start the API service if it is not already running and healthy."""

        probe = self.probe()
        if not probe.ok:
            raise GPTSoVITSNotInstalled(probe.error or "GPT-SoVITS 安装不完整")
        with self._lock:
            if self.is_alive():
                return
            if self._process is not None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                self._process = None
            log_path = self.project_root / "data" / "logs" / "gpt-sovits-api.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_handle = open(log_path, "a", encoding="utf-8", errors="replace")
            env = os.environ.copy()
            env.setdefault("PYTHONUNBUFFERED", "1")
            self._process = subprocess.Popen(
                [
                    str(probe.python_path),
                    str(probe.api_script),
                    "-a",
                    "127.0.0.1",
                    "-p",
                    str(self.port),
                ],
                cwd=str(probe.install_dir),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=env,
            )
            self._attach_kill_on_close_job(self._process)
            # Cold start loads the default v2Pro weights + vocoder, which can
            # take several minutes on first launch.
            for _ in range(600):
                if self._process.poll() is not None:
                    break
                try:
                    with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                        pass
                    return
                except OSError:
                    self.sleeper(0.5)
            raise GPTSoVITSUnavailable(
                "GPT-SoVITS API 服务启动失败，请查看日志：data/logs/gpt-sovits-api.log"
            )

    def stop_service(self) -> None:
        with self._lock:
            if self._process is None:
                # 仍可能残留未追踪的服务（历史孤儿），兜底清理端口监听者。
                self._kill_listener()
                self._loaded_gpt = None
                self._loaded_sovits = None
                self._loaded_refer = None
                return
            process = self._process
            self._process = None
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
            self._close_job()
            self._kill_listener()
            self._loaded_gpt = None
            self._loaded_sovits = None
            self._loaded_refer = None

    # ------------------------------------------------------------------
    # inference
    # ------------------------------------------------------------------

    def set_model(
        self,
        gpt_weights: str,
        sovits_weights: str,
        refer_audio: str | None = None,
    ) -> None:
        """Load weights into the API process (no-op when already loaded)."""

        self.ensure_service()
        if gpt_weights != self._loaded_gpt:
            self._request("/set_gpt_weights", {"weights_path": gpt_weights})
            self._loaded_gpt = gpt_weights
        if sovits_weights != self._loaded_sovits:
            self._request("/set_sovits_weights", {"weights_path": sovits_weights})
            self._loaded_sovits = sovits_weights
        # The engine re-extracts the reference audio features on every
        # set_refer_audio call (CNHuBERT semantic + mel spec); skip it when
        # the same reference was already loaded for this voice.
        if refer_audio and refer_audio != self._loaded_refer:
            self._request("/set_refer_audio", {"refer_audio_path": refer_audio})
            self._loaded_refer = refer_audio

    def synthesize(
        self,
        text: str,
        *,
        text_lang: str = "zh",
        gpt_weights: str,
        sovits_weights: str,
        refer_audio: str | None = None,
        prompt_lang: str = "zh",
        prompt_text: str = "",
        streaming: bool = False,
    ) -> bytes:
        """Synthesize text into WAV bytes with the given voice asset."""

        self.set_model(gpt_weights, sovits_weights, refer_audio)
        payload = {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": refer_audio or "",
            "prompt_lang": prompt_lang,
            "prompt_text": prompt_text,
            "media_type": "wav",
            "streaming_mode": streaming,
        }
        try:
            return self._request("/tts", payload=payload)
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            raise GPTSoVITSUnavailable(f"GPT-SoVITS 合成失败：{detail}") from exc
        except (OSError, URLError, ValueError) as exc:
            raise GPTSoVITSUnavailable(f"GPT-SoVITS 服务不可用：{exc}") from exc

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def status(self) -> dict:
        probe = self.probe()
        alive = self.is_alive()
        return {
            "configured": bool(self.config.values()["install_dir"]),
            "installed": probe.ok,
            "ready": probe.ok and alive,
            "install_dir": str(probe.install_dir) if probe.install_dir else None,
            "python_path": str(probe.python_path) if probe.python_path else None,
            "api_script": str(probe.api_script) if probe.api_script else None,
            "api_version": probe.api_version,
            "api_port": self.port,
            "service_running": alive,
            "error": probe.error,
        }
