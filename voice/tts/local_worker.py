import atexit
import base64
import ctypes
import io
import json
import os
import socket
import subprocess
import sys
import threading
import time
import wave
from ctypes import wintypes
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TTSGenerationError(RuntimeError):
    pass


# Qwen3-TTS 12Hz：1 秒音频 = 12.5 个音频 token。
# 引擎采样状态不固定：正常时自然 EOS，异常时会把全部配额"说满"。
# 160 是历史会话验证过的上限（约 12.8 秒），保持该值控制坏采样的损害；
# 长回复改由分段合成保证完整。
DEFAULT_MAX_AUDIO_TOKENS = 160
# 单段文本字符上限（约 12.5 秒音频，低于 160 token 上限），
# 超出按句子边界切分后逐段合成并拼接。
DEFAULT_CHUNK_CHARS = 50
# 单段输出时长的绝对下限（秒）。极短回复（如"好的"）自然时长可低于 1 秒，
# 因此取下限 0.3s，避免误触发短输出重试。
MIN_AUDIO_SECONDS = 0.3
# 输出时长低于"文本估时 * 0.3"视为异常采样（引擎 RNG 共享导致偶发提前 EOS）。
SHORT_OUTPUT_FACTOR = 0.3
# 引擎采样状态随机（无 seed 控制、RNG 跨调用共享），同文本可能自然结束、
# 提前 EOS 或一路说满上限；每次重启服务都会重置 RNG。
# 重试是"抽签"，多次重试收益不稳且耗时高，因此只额外尝试 1 次。
MAX_SYNTH_ATTEMPTS = 2


def _create_kill_on_close_job() -> int | None:
    """创建 Windows Job Object（JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE）。

    把 TTS 子进程放入该 Job 后，只要父进程退出（无论正常退出还是被强杀），
    Job 句柄关闭都会自动结束子进程，避免遗留孤儿进程占用 GPU/内存。
    """
    if os.name != "nt":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in (
                    "ReadOperationCount",
                    "WriteOperationCount",
                    "OtherOperationCount",
                    "ReadTransferCount",
                    "WriteTransferCount",
                    "OtherTransferCount",
                )
            ]

        class BASIC_LIMITS(ctypes.Structure):
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

        class EXT_LIMITS(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMITS),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = EXT_LIMITS()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):
            kernel32.CloseHandle(job)
            return None
        return int(job)
    except Exception:  # pragma: no cover - 极端环境降级为无 Job 保护
        return None


def _assign_process_to_job(job: int | None, pid: int) -> None:
    """把子进程挂到 Job 上；失败仅静默降级（不影响主流程）。"""
    if not job:
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_TERMINATE = 0x0400
        handle = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not handle:
            return
        try:
            kernel32.AssignProcessToJobObject(int(job), handle)
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # pragma: no cover
        pass


def _close_job_handle(job: int | None) -> None:
    """关闭 Job 句柄；若仍有子进程在 Job 内，KILL_ON_JOB_CLOSE 会一并结束它们。"""
    if not job:
        return
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle(int(job))
    except Exception:  # pragma: no cover
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
        self._watchdog: subprocess.Popen | None = None
        self._using_gpu: bool | None = None
        self._process_lock = threading.Lock()
        self._job_handle = _create_kill_on_close_job()
        project_root = self.model_dir.parents[1] if self.model_dir.parent.name == "models" else self.model_dir.parent
        self.log_path = project_root / "data" / "logs" / "tts-service.log"
        atexit.register(self.stop_service)

        self.max_tokens = DEFAULT_MAX_AUDIO_TOKENS
        self.chunk_chars = DEFAULT_CHUNK_CHARS

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

    @staticmethod
    def _worker_python(project_root: Path) -> Path:
        """返回可执行 worker 脚本的 Python 解释器路径。"""
        if getattr(sys, "frozen", False):
            candidate = project_root / ".venv" / "Scripts" / "python.exe"
            if candidate.is_file():
                return candidate
        return Path(sys.executable)

    def _spawn_watchdog(self, project_root: Path, child_pid: int) -> None:
        """拉起/替换子进程守护，保证父进程退出后 TTS 服务随之结束。"""
        try:
            if self._watchdog is not None and self._watchdog.poll() is None:
                self._watchdog.terminate()
            watchdog_script = project_root / "voice" / "child_watchdog.py"
            if not watchdog_script.is_file():
                return
            kwargs = {}
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            self._watchdog = subprocess.Popen(
                [
                    str(self._worker_python(project_root)),
                    "-B",
                    str(watchdog_script),
                    "--parent",
                    str(os.getpid()),
                    "--child",
                    str(child_pid),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **kwargs,
            )
        except Exception:  # pragma: no cover - 守护失败不影响主流程
            self._watchdog = None

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
                    self.log_path.parent.mkdir(parents=True, exist_ok=True)
                    log_handle = open(self.log_path, "a", encoding="utf-8", errors="replace")
                    self._process = self.process_factory(
                        [str(self.runtime_path), "--basic-port", str(self.port), "--local-dir", str(project_root)],
                        cwd=str(self.runtime_path.parent),
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        env=environment,
                    )
                    _assign_process_to_job(self._job_handle, self._process.pid)
                    self._spawn_watchdog(project_root, self._process.pid)
                    self._using_gpu = self.use_gpu
                for _ in range(50):
                    if self._is_ready():
                        return
                    self.sleeper(0.1)
        raise TTSGenerationError("Lunar TTS 服务启动超时")

    def warm_up(self) -> None:
        """Start the TTS service and load the model ahead of the first request."""
        self._ensure_ready()

    def _restart_service(self) -> None:
        """Terminate a crashed/unresponsive service so the next call respawns it."""
        with self._process_lock:
            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
            self._process = None
            self._using_gpu = None
        self._ensure_ready()

    # ------------------------------------------------------------------
    # 文本分段与输出校验
    # ------------------------------------------------------------------

    @staticmethod
    def split_chunks(text: str, max_chars: int) -> list[str]:
        """按句子边界把文本切成不超过 max_chars 的片段。"""
        stripped = text.strip()
        if not stripped:
            return []
        if len(stripped) <= max_chars:
            return [stripped]
        pieces: list[str] = []
        current = ""
        for char in stripped:
            current += char
            if char in "。！？；\n" or len(current) >= max_chars:
                piece = current.strip()
                if piece:
                    pieces.append(piece)
                current = ""
        if current.strip():
            pieces.append(current.strip())
        return pieces

    @staticmethod
    def stream_segments(text: str, max_chars: int = 50) -> list[str]:
        """面向流式合成的句子级切分。

        以句号/感叹号/问号/分号/省略号/换行作为边界；短句合并到 max_chars，
        超长句硬切。每段控制在约 12 秒语音以内（50 字 ≈ 12.5s），保证单段
        不超过引擎 max_tokens 上限并能自然结束。
        """
        stripped = text.strip()
        if not stripped:
            return []
        raw: list[str] = []
        current = ""
        for char in stripped:
            current += char
            if char in "。！？；…\n" or len(current) >= max_chars:
                piece = current.strip()
                if piece:
                    raw.append(piece)
                current = ""
        if current.strip():
            raw.append(current.strip())
        merged: list[str] = []
        for piece in raw:
            if merged and len(merged[-1]) + len(piece) <= max_chars:
                merged[-1] += piece
            else:
                merged.append(piece)
        return merged

    @staticmethod
    def estimate_min_audio_seconds(text: str) -> float:
        """粗略估计文本的语音时长下限：中文约 4 字/秒，其余约 10 字/秒。"""
        chinese = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        other = max(0, len(text) - chinese)
        return max(MIN_AUDIO_SECONDS, (chinese / 4.0 + other / 10.0) * SHORT_OUTPUT_FACTOR)

    @staticmethod
    def wav_duration(audio: bytes) -> float:
        try:
            with wave.open(io.BytesIO(audio), "rb") as source:
                if source.getnchannels() != 1 or source.getsampwidth() != 2:
                    raise TTSGenerationError("语音服务返回了非预期的音频格式")
                return source.getnframes() / float(source.getframerate())
        except (wave.Error, EOFError) as exc:
            raise TTSGenerationError("语音服务返回了无效的 WAV 音频") from exc

    @staticmethod
    def merge_wavs(parts: list[bytes]) -> bytes:
        """把多段同格式 WAV 拼接成一个 24kHz 单声道 16-bit WAV。"""
        if len(parts) == 1:
            return parts[0]
        frames = bytearray()
        rate = 24000
        for audio in parts:
            with wave.open(io.BytesIO(audio), "rb") as source:
                if source.getnchannels() != 1 or source.getsampwidth() != 2:
                    raise TTSGenerationError("语音服务返回了非预期的音频格式")
                rate = source.getframerate()
                frames.extend(source.readframes(source.getnframes()))
        output = io.BytesIO()
        with wave.open(output, "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(rate)
            target.writeframes(bytes(frames))
        return output.getvalue()

    # ------------------------------------------------------------------
    # 合成
    # ------------------------------------------------------------------

    def _synthesize_request(self, text: str, reference_audio: Path | None) -> bytes:
        payload = {"text": text.strip(), "max_tokens": int(self.max_tokens)}
        if reference_audio:
            payload["ref_audio"] = str(reference_audio)
        try:
            response = self._request("/tts", payload)
        except HTTPError:
            raise
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            self._restart_service()
            try:
                response = self._request("/tts", payload)
            except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
                raise TTSGenerationError(f"本地语音生成失败：{exc}") from exc
        if not response.get("success"):
            raise TTSGenerationError(str(response.get("error") or "Lunar TTS 合成失败"))
        try:
            audio = base64.b64decode(str(response.get("audio") or ""), validate=True)
        except (OSError, ValueError) as exc:
            raise TTSGenerationError(f"本地语音生成失败：{exc}") from exc
        if not audio:
            raise TTSGenerationError("Lunar TTS 没有返回音频")
        return audio

    def _synthesize_chunk(self, text: str, reference_audio: Path | None) -> bytes:
        """合成单个文本片段，异常采样（过短/失控说满）时重启服务重试。"""
        minimum = self.estimate_min_audio_seconds(text)
        cap_seconds = self.max_tokens / 12.5
        expected_upper = max(1.0, len(text) / 3.0)  # 文本时长的宽松估计上限
        last_audio: bytes | None = None
        for attempt in range(MAX_SYNTH_ATTEMPTS):
            audio = self._synthesize_request(text, reference_audio)
            duration = self.wav_duration(audio)
            last_audio = audio
            runaway = duration >= cap_seconds - 0.5 and expected_upper < cap_seconds * 0.6
            if duration >= minimum and not runaway:
                return audio
            if attempt < MAX_SYNTH_ATTEMPTS - 1:
                reason = "音频过短" if duration < minimum else "短文本却说满上限"
                print(
                    f"[tts] 异常采样（{reason}，{duration:.1f}s，文本 {len(text)} 字），"
                    f"重启服务重试 {attempt + 2}/{MAX_SYNTH_ATTEMPTS}",
                    flush=True,
                )
                self._restart_service()
                continue
            print(f"[tts] 重试后仍异常（{duration:.1f}s），按可用性保底返回", flush=True)
            return audio
        if last_audio is None:  # pragma: no cover
            raise TTSGenerationError("合成失败")
        return last_audio

    def synthesize(self, text: str, output: Path, reference_audio: Path | None = None) -> Path:
        if not text.strip():
            raise TTSGenerationError("合成文本为空")
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_ready()
        chunks = self.split_chunks(text, self.chunk_chars)
        if not chunks:
            raise TTSGenerationError("合成文本为空")
        parts = [self._synthesize_chunk(chunk, reference_audio) for chunk in chunks]
        merged = self.merge_wavs(parts)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_bytes(merged)
        temporary.replace(output)
        return output

    def stop_service(self) -> None:
        with self._process_lock:
            if self._watchdog is not None and self._watchdog.poll() is None:
                self._watchdog.terminate()
            self._watchdog = None
            if self._process is not None and self._process.poll() is None:
                self._process.terminate()
            self._process = None
            self._using_gpu = None
        if self._job_handle:
            _close_job_handle(self._job_handle)
            self._job_handle = None
