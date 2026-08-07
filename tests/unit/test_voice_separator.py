import io
import wave
from pathlib import Path

import numpy as np
import pytest


def stereo_wav(frames: np.ndarray, rate: int = 44100) -> bytes:
    """frames: (2, N) float32 in [-1, 1]."""
    pcm = np.clip(frames, -1.0, 1.0)
    pcm = (pcm.T * 32767.0).astype(np.int16)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm.tobytes())
    return stream.getvalue()


class IdentitySession:
    """Tiny fake ONNX session: vocals stem = input mix, other stems zero."""

    def __init__(self, n_sources: int = 4) -> None:
        self.n_sources = n_sources

    def run(self, output_names, input_feed):
        mix = input_feed["mix"]
        stems = np.zeros((mix.shape[0], self.n_sources, *mix.shape[1:]), dtype=np.float32)
        stems[:, 3] = mix
        return [stems]


def test_transition_window_is_symmetric_and_unit_middle():
    from voice.separator.onnx import _make_transition_window

    segment = 8000
    window = _make_transition_window(segment, overlap_frac=0.25)
    transition = segment // 4
    assert window.shape == (segment,)
    assert window[0] == pytest.approx(0.0)
    assert window[-1] == pytest.approx(0.0)
    assert window[transition : segment - transition].min() == pytest.approx(1.0)
    assert window[:transition] == pytest.approx(window[-transition:][::-1])


def test_separate_chunked_preserves_vocals_identity(tmp_path):
    from voice.separator.onnx import HtdemucsSeparator

    mix = np.stack(
        [
            0.5 * np.sin(2 * np.pi * 220 * np.arange(44100 * 3) / 44100),
            0.3 * np.sin(2 * np.pi * 330 * np.arange(44100 * 3) / 44100),
        ],
        axis=0,
    ).astype(np.float32)
    source = tmp_path / "mix.wav"
    source.write_bytes(stereo_wav(mix))
    target = tmp_path / "vocals.wav"

    separator = HtdemucsSeparator(Path("unused.onnx"), session_factory=lambda _: IdentitySession())
    separator.separate(source, target)

    with wave.open(str(target), "rb") as output:
        assert output.getnchannels() == 2
        assert output.getframerate() == 44100
        frames = np.frombuffer(output.readframes(output.getnframes()), dtype=np.int16).reshape(-1, 2)
    assert frames.shape[0] == mix.shape[1]
    # vocals stem equals the input mix, so peak error stays tiny.
    assert float(np.abs(frames.astype(np.float32) / 32767.0 - mix.T).max()) < 1e-3


def test_separate_rejects_non_44100_wav(tmp_path):
    from voice.separator.onnx import HtdemucsSeparator

    source = tmp_path / "mix.wav"
    source.write_bytes(stereo_wav(np.zeros((2, 8000), dtype=np.float32), rate=16000))
    separator = HtdemucsSeparator(Path("unused.onnx"), session_factory=lambda _: IdentitySession())
    with pytest.raises(ValueError):
        separator.separate(source, tmp_path / "vocals.wav")


def test_separate_with_real_model_if_present(tmp_path):
    """Integration check against the actual downloaded model when available."""
    import onnxruntime as ort

    candidates = [
        Path(r"C:\Users\TKGEKKOU\AppData\Local\Temp\htdemucs_ft_vocals_fp16weights.onnx"),
        Path("models/separator/htdemucs_ft_vocals_fp16weights.onnx"),
    ]
    model = next((path for path in candidates if path.is_file()), None)
    if model is None or not ort.get_available_providers():
        pytest.skip("real htdemucs model not available")

    from voice.separator.onnx import HtdemucsSeparator

    t = np.arange(44100 * 4) / 44100
    mix = np.stack([0.4 * np.sin(2 * np.pi * 200 * t), 0.4 * np.sin(2 * np.pi * 300 * t)], axis=0).astype(np.float32)
    source = tmp_path / "mix.wav"
    source.write_bytes(stereo_wav(mix))
    target = tmp_path / "vocals.wav"
    HtdemucsSeparator(model).separate(source, target)
    with wave.open(str(target), "rb") as output:
        assert output.getnframes() == mix.shape[1]
