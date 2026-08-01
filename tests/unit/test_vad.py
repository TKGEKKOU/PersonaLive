import numpy as np
import pytest
import torch

from voice.vad.energy import EnergyVAD
from voice.vad.silero import SileroVAD


def _tone(seconds: float, amplitude: float = 0.4, rate: int = 16000, freq: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (np.sin(2 * np.pi * freq * t) * amplitude).astype(np.float32)


def test_energy_vad_detects_utterance_between_silence():
    vad = EnergyVAD(min_speech_ms=90, min_silence_ms=200)
    rng = np.random.default_rng(0)
    silence = rng.uniform(-0.001, 0.001, 8000).astype(np.float32)
    tone = _tone(0.5)
    events = []
    for chunk in (silence, tone, silence):
        events.extend(vad.process(chunk))

    kinds = [event.kind for event in events]
    assert kinds == ["speech_start", "speech_stop"]
    start, stop = events
    assert 8200 < start.sample_index < 9800  # a few frames after tone onset at 8000
    assert 16500 < stop.sample_index < 20000  # after tone end at 16000 plus min silence


def test_energy_vad_reset_clears_state():
    vad = EnergyVAD()
    vad.process(_tone(1.0))
    vad.reset()
    events = vad.process(np.zeros(16000, dtype=np.float32))
    assert events == []


class FakeSileroModel:
    """Stand-in for silero's jit model: returns a scripted probability per frame."""

    def __init__(self, probs):
        self.probs = iter(probs)

    def reset_states(self):
        pass

    def __call__(self, x, sr):
        return torch.tensor([next(self.probs)])


def test_silero_vad_maps_events_and_sample_indices():
    from voice.vad.silero import SileroVAD

    # 3 quiet frames, 8 speech frames, then long silence
    probs = [0.05] * 3 + [0.95] * 8 + [0.05] * 30
    vad = SileroVAD(model=FakeSileroModel(probs), min_silence_ms=200, speech_pad_ms=30)
    events = []
    for _ in range(5):
        events.extend(vad.process(np.zeros(512 * 8, dtype=np.float32)))

    kinds = [event.kind for event in events]
    assert kinds == ["speech_start", "speech_stop"]
    start, stop = events
    assert start.sample_index > 0
    assert stop.sample_index > start.sample_index


def test_silero_vad_reset_restarts_sample_index():
    probs = [0.95] * 8 + [0.05] * 30
    vad = SileroVAD(model=FakeSileroModel(probs), min_silence_ms=200)
    vad.process(np.zeros(512 * 8, dtype=np.float32))
    vad.reset()
    assert vad.process(np.zeros(512 * 4, dtype=np.float32)) == []


def test_silero_vad_detects_real_speech():
    """Integration smoke: the bundled model plus our wrapper must segment real speech."""

    pytest.importorskip("silero_vad")
    from pathlib import Path
    import wave

    path = Path(__file__).resolve().parents[2] / "audios" / "lunar-template.wav"
    if not path.is_file():
        pytest.skip("lunar-template.wav is not present")
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        channels = source.getnchannels()
        frames = source.readframes(source.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if rate != 16000:
        positions = np.linspace(0, len(audio) - 1, int(len(audio) * 16000 / rate))
        audio = np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)

    vad = SileroVAD(min_silence_ms=600)
    events = []
    for index in range(0, len(audio), 2048):
        events.extend(vad.process(audio[index : index + 2048]))

    kinds = [event.kind for event in events]
    assert kinds[:4] == ["speech_start", "speech_stop", "speech_start", "speech_stop"]
    assert events[0].sample_index < events[1].sample_index < events[2].sample_index
