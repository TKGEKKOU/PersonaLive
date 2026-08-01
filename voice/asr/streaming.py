from __future__ import annotations

from collections.abc import Awaitable, Callable

import numpy as np

from voice.asr.base import ASREmptyResultError

SAMPLE_RATE = 16000
MAX_UTTERANCE_SECONDS = 60


class StreamSession:
    """Holds the accumulated PCM for one streaming ASR utterance.

    infer(accumulated_pcm) is an injected async callable that returns
    (language, text); the worker wires it to the real model behind a lock and
    a thread, while unit tests inject a fake. Keeping the session logic here
    makes the streaming protocol testable without a GPU.
    """

    def __init__(
        self,
        infer: Callable[[np.ndarray], Awaitable[tuple[str, str]]],
        max_seconds: int = MAX_UTTERANCE_SECONDS,
    ) -> None:
        self._infer = infer
        self._max_samples = max_seconds * SAMPLE_RATE
        self._audio = np.zeros(0, dtype=np.float32)

    @property
    def audio(self) -> np.ndarray:
        return self._audio

    async def feed(self, pcm: np.ndarray) -> None:
        samples = np.asarray(pcm)
        if samples.dtype == np.int16:
            samples = samples.astype(np.float32) / 32768.0
        else:
            samples = samples.astype(np.float32, copy=False)
        samples = samples.reshape(-1)
        if samples.size == 0:
            return
        if self._audio.shape[0] + samples.shape[0] > self._max_samples:
            raise ValueError("utterance_too_long")
        self._audio = np.concatenate([self._audio, samples])

    async def transcribe(self) -> tuple[str, str]:
        if self._audio.shape[0] == 0:
            raise ASREmptyResultError("No speech was recognized")
        return await self._infer(self._audio)

    def cancel(self) -> None:
        self._audio = np.zeros(0, dtype=np.float32)
