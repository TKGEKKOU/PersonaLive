"""HT-Demucs FT vocals specialist running on pure ONNX Runtime.

Mirrors the reference implementation published with the model
(StemSplitio/htdemucs-ft-vocals-onnx): chunked overlap-add inference over
7.8 s segments with a 25% transition window. No PyTorch is required.
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Callable

import numpy as np

SAMPLE_RATE = 44100
SEGMENT_SECONDS = 7.8
SEGMENT_SAMPLES = int(SEGMENT_SECONDS * SAMPLE_RATE)
SOURCES = ("drums", "bass", "other", "vocals")
SPECIALIST_STEM = "vocals"


def _make_transition_window(segment: int, overlap_frac: float = 0.25) -> np.ndarray:
    transition = int(segment * overlap_frac)
    window = np.ones(segment, dtype=np.float32)
    fade = np.linspace(0, 1, transition, dtype=np.float32)
    window[:transition] = fade
    window[-transition:] = fade[::-1]
    return window


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Read a WAV file as float32 channels-first array (channels, samples)."""
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        channels = source.getnchannels()
        width = source.getsampwidth()
        if channels not in (1, 2):
            raise ValueError(f"expected mono or stereo WAV, got {channels} channels")
        raw = source.readframes(source.getnframes())
    if width == 2:
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        samples = np.frombuffer(raw, dtype=np.float32).astype(np.float32)
    else:
        raise ValueError(f"unsupported WAV sample width {width}")
    samples = samples.reshape(-1, channels).T
    if channels == 1:
        samples = np.tile(samples, (2, 1))
    return samples, rate


def _write_wav(path: Path, samples: np.ndarray, rate: int) -> None:
    """Write a float32 channels-first array as a 16-bit WAV."""
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm.T * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(pcm.shape[1])
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(pcm.tobytes())


class HtdemucsSeparator:
    """Chunked HT-Demucs vocals separation on ONNX Runtime."""

    def __init__(
        self,
        model_path: Path,
        providers: list[str] | None = None,
        session_factory: Callable[[Path], object] | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self._providers = providers or ["CPUExecutionProvider"]
        self._session_factory = session_factory
        self._session: object | None = None

    @classmethod
    def available_providers(cls) -> list[str]:
        import onnxruntime as ort

        return ort.get_available_providers()

    def _ensure_session(self):
        if self._session is None:
            if self._session_factory is not None:
                self._session = self._session_factory(self.model_path)
            else:
                import onnxruntime as ort

                self._session = ort.InferenceSession(str(self.model_path), providers=self._providers)
        return self._session

    def separate(
        self,
        input_wav: Path,
        output_wav: Path,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Extract the vocals stem from a 44.1 kHz stereo WAV and save it.

        Returns the output path. Raises ValueError for unsupported input.
        """
        mix, rate = _read_wav(Path(input_wav))
        if rate != SAMPLE_RATE:
            raise ValueError(f"separator is bound to {SAMPLE_RATE} Hz; got {rate}")
        session = self._ensure_session()
        total_len = mix.shape[1]
        overlap = SEGMENT_SAMPLES // 4
        stride = SEGMENT_SAMPLES - overlap
        n_chunks = max(1, (total_len + stride - 1) // stride)
        window = _make_transition_window(SEGMENT_SAMPLES)

        out = np.zeros((len(SOURCES), 2, total_len), dtype=np.float32)
        weight = np.zeros(total_len, dtype=np.float32)
        for index in range(n_chunks):
            start = index * stride
            end = min(start + SEGMENT_SAMPLES, total_len)
            chunk = mix[:, start:end]
            if chunk.shape[1] < SEGMENT_SAMPLES:
                chunk = np.pad(chunk, ((0, 0), (0, SEGMENT_SAMPLES - chunk.shape[1])), mode="constant")
            stems = session.run(["stems"], {"mix": chunk[np.newaxis, ...].astype(np.float32)})[0][0]
            chunk_len = end - start
            segment_window = window[:chunk_len]
            out[:, :, start:end] += stems[:, :, :chunk_len] * segment_window
            weight[start:end] += segment_window
            if progress is not None:
                progress(index + 1, n_chunks)

        weight = np.maximum(weight, 1e-8)
        out /= weight
        vocals = out[SOURCES.index(SPECIALIST_STEM)]
        Path(output_wav).parent.mkdir(parents=True, exist_ok=True)
        _write_wav(Path(output_wav), vocals, rate)
        return Path(output_wav)
