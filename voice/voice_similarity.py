"""Voice-timbre similarity scoring via the bundled Lunar TTS C++ DLL.

Extracts the same 1024-dim speaker embedding the engine uses for cloning and
scores two wavs with cosine similarity. No PyTorch and no extra model needed;
the model is loaded lazily on first use and kept resident for repeat scoring.
"""

from __future__ import annotations

import ctypes
import hashlib
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

EMBEDDING_MAX = 2048


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 1e-9:
        return 0.0
    return float(np.dot(a, b) / denominator)


class VoiceEmbeddingEngine:
    """Lazy-loaded ctypes wrapper around qwen3tts.dll embedding extraction."""

    def __init__(self, dll_path: Path, model_dir: Path, n_threads: int = 8) -> None:
        self._dll = ctypes.CDLL(str(Path(dll_path).resolve()))
        self._model_dir = str(Path(model_dir).resolve())
        self._n_threads = n_threads
        self._handle: int | None = None
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._bind()

    def _bind(self) -> None:
        self._dll.qwen3_tts_create.restype = ctypes.c_void_p
        self._dll.qwen3_tts_create.argtypes = [ctypes.c_char_p, ctypes.c_int32]
        self._dll.qwen3_tts_extract_embedding_file.restype = ctypes.c_int32
        self._dll.qwen3_tts_extract_embedding_file.argtypes = [
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_float),
            ctypes.c_int32,
        ]
        self._dll.qwen3_tts_destroy.restype = None
        self._dll.qwen3_tts_destroy.argtypes = [ctypes.c_void_p]

    def _ensure(self) -> None:
        if self._handle is not None:
            return
        handle = self._dll.qwen3_tts_create(self._model_dir.encode("utf-8"), self._n_threads)
        if not handle:
            raise RuntimeError("语音引擎初始化失败")
        self._handle = handle

    def extract(self, wav_path: Path) -> np.ndarray:
        """Extract the speaker embedding for a wav (24k mono recommended)."""
        path = Path(wav_path).resolve()
        key = hashlib.sha256(path.read_bytes()).hexdigest()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        with self._lock:
            self._ensure()
            buffer = (ctypes.c_float * EMBEDDING_MAX)()
            size = self._dll.qwen3_tts_extract_embedding_file(
                self._handle,
                str(path).encode("utf-8"),
                buffer,
                EMBEDDING_MAX,
            )
            if size <= 0:
                raise RuntimeError("无法提取音频特征")
            embedding = np.array(buffer[:size], dtype=np.float32)
            self._cache[key] = embedding
            if len(self._cache) > 16:
                self._cache.popitem(last=False)
            return embedding

    def similarity(self, audio_a: Path, audio_b: Path) -> float:
        return cosine_similarity(self.extract(audio_a), self.extract(audio_b))

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                self._dll.qwen3_tts_destroy(self._handle)
                self._handle = None
