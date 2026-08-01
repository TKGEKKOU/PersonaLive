from __future__ import annotations

from typing import Any

import numpy as np
import torch

from voice.vad.base import VAD, VADEvent

WINDOW_SIZE = 512  # silero 16 kHz models expect 512-sample frames
FRAME_MS = WINDOW_SIZE * 1000 / 16000  # 32 ms

_MODEL: Any = None


def _get_model() -> Any:
    """Load the bundled silero JIT model once and share it across connections.

    The model is stateless; per-connection state lives in the VADIterator
    instances created below, so sharing the model is safe.
    """

    global _MODEL
    if _MODEL is None:
        from silero_vad import load_silero_vad

        _MODEL = load_silero_vad()
    return _MODEL


class SileroVAD(VAD):
    """Silero VAD with an explicit onset/offset state machine.

    The raw model emits a speech probability per 512-sample window; this class
    applies its own hysteresis so a burst of noise has to persist for
    min_speech_ms before an utterance starts, and speech has to stay silent for
    min_silence_ms before it ends. That reduces false triggers compared to
    triggering on a single loud frame.
    """

    def __init__(
        self,
        model: Any | None = None,
        threshold: float = 0.5,
        min_speech_ms: int = 128,
        min_silence_ms: int = 600,
        speech_pad_ms: int = 200,
    ) -> None:
        self._model = model if model is not None else _get_model()
        self._model.reset_states()
        self._threshold = threshold
        self._silence_threshold = max(0.05, threshold - 0.15)
        self._min_speech_frames = max(1, int(min_speech_ms / FRAME_MS))
        self._min_silence_frames = max(1, int(min_silence_ms / FRAME_MS))
        self._speech_pad_samples = int(self.sample_rate * speech_pad_ms / 1000)
        self._buffer = np.zeros(0, dtype=np.float32)
        self._samples_seen = 0
        self._speech_frames = 0
        self._silence_frames = 0
        self._onset_start = 0
        self._speech_active = False

    def reset(self) -> None:
        self._model.reset_states()
        self._buffer = np.zeros(0, dtype=np.float32)
        self._samples_seen = 0
        self._speech_frames = 0
        self._silence_frames = 0
        self._onset_start = 0
        self._speech_active = False

    def process(self, pcm: np.ndarray) -> list[VADEvent]:
        samples = np.asarray(pcm)
        if samples.dtype == np.int16:
            samples = samples.astype(np.float32) / 32768.0
        else:
            samples = samples.astype(np.float32, copy=False)
        samples = np.concatenate([self._buffer, samples.reshape(-1)])
        events: list[VADEvent] = []
        while samples.shape[0] >= WINDOW_SIZE:
            frame = samples[:WINDOW_SIZE]
            samples = samples[WINDOW_SIZE:]
            frame_start = self._samples_seen
            self._samples_seen += WINDOW_SIZE
            probability = float(self._model(torch.from_numpy(frame), self.sample_rate).item())
            is_speech = probability >= self._threshold
            is_silence = probability < self._silence_threshold
            if self._speech_active:
                if is_silence:
                    self._silence_frames += 1
                    if self._silence_frames >= self._min_silence_frames:
                        self._speech_active = False
                        self._speech_frames = 0
                        self._silence_frames = 0
                        events.append(VADEvent("speech_stop", frame_start))
                elif is_speech:
                    self._silence_frames = 0
                continue
            if is_speech:
                if self._speech_frames == 0:
                    self._onset_start = frame_start
                self._speech_frames += 1
                if self._speech_frames >= self._min_speech_frames:
                    self._speech_active = True
                    self._silence_frames = 0
                    start = max(0, self._onset_start - self._speech_pad_samples)
                    events.append(VADEvent("speech_start", start))
            else:
                self._speech_frames = 0
        self._buffer = samples
        return events
