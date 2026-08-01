from __future__ import annotations

import numpy as np

from voice.vad.base import VAD, VADEvent


class EnergyVAD(VAD):
    """Dependency-free RMS/energy VAD with an adaptive noise floor.

    This is a fallback for environments without silero-vad and the default in
    unit tests. It is deliberately conservative: a frame counts as speech only
    when its RMS exceeds both an absolute floor and the running noise floor.
    """

    def __init__(
        self,
        frame_ms: int = 30,
        speech_rms: float = 0.02,
        noise_ratio: float = 4.0,
        min_speech_ms: int = 120,
        min_silence_ms: int = 600,
    ) -> None:
        self._frame_size = int(self.sample_rate * frame_ms / 1000)
        self._speech_rms = speech_rms
        self._noise_ratio = noise_ratio
        self._min_speech_frames = max(1, int(min_speech_ms / frame_ms))
        self._min_silence_frames = max(1, int(min_silence_ms / frame_ms))
        self._buffer = np.zeros(0, dtype=np.float32)
        self._noise_floor = 1e-4
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_active = False
        self._samples_seen = 0

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self._noise_floor = 1e-4
        self._speech_frames = 0
        self._silence_frames = 0
        self._speech_active = False
        self._samples_seen = 0

    def process(self, pcm: np.ndarray) -> list[VADEvent]:
        samples = np.asarray(pcm)
        if samples.dtype == np.int16:
            samples = samples.astype(np.float32) / 32768.0
        else:
            samples = samples.astype(np.float32, copy=False)
        samples = np.concatenate([self._buffer, samples.reshape(-1)])
        events: list[VADEvent] = []
        while samples.shape[0] >= self._frame_size:
            frame = samples[: self._frame_size]
            samples = samples[self._frame_size :]
            rms = float(np.sqrt(np.mean(np.square(frame), dtype=np.float64)))
            self._noise_floor = max(1e-4, min(self._noise_floor, rms))
            threshold = max(self._speech_rms, self._noise_floor * self._noise_ratio)
            frame_start = self._samples_seen
            self._samples_seen += self._frame_size
            if rms >= threshold:
                self._silence_frames = 0
                self._speech_frames += 1
                if not self._speech_active and self._speech_frames >= self._min_speech_frames:
                    self._speech_active = True
                    events.append(VADEvent("speech_start", frame_start))
            else:
                self._speech_frames = 0
                if self._speech_active:
                    self._silence_frames += 1
                    if self._silence_frames >= self._min_silence_frames:
                        self._speech_active = False
                        events.append(VADEvent("speech_stop", frame_start))
        self._buffer = samples
        return events
