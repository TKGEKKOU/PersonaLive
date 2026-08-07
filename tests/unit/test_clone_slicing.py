import io
import wave
from pathlib import Path

import numpy as np
import pytest


def mono_wav_bytes(frames: np.ndarray, rate: int) -> bytes:
    pcm = np.clip(frames, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm.tobytes())
    return stream.getvalue()


def speech_like(duration: float, rate: int, bursts) -> np.ndarray:
    """bursts: list of (start_sec, dur_sec, amplitude)."""
    samples = np.zeros(int(duration * rate), dtype=np.float32)
    for start, dur, amp in bursts:
        lo, hi = int(start * rate), int((start + dur) * rate)
        t = np.arange(hi - lo) / rate
        samples[lo:hi] = amp * (np.sin(2 * np.pi * 180 * t) + 0.2 * np.sin(2 * np.pi * 340 * t))
    return samples


def test_slice_speech_returns_clean_segments_in_order(tmp_path):
    from voice.clone_pipeline import slice_speech
    from voice.vad.energy import EnergyVAD

    rate = 16000
    audio = speech_like(20.0, rate, [(1.0, 5.0, 0.5), (9.0, 8.0, 0.8)])
    path = tmp_path / "speech.wav"
    path.write_bytes(mono_wav_bytes(audio, rate))

    segments = slice_speech(path, EnergyVAD(), min_segment=2.0, max_segment=12.0, min_total=5.0)
    assert len(segments) == 2
    assert segments[0].start_24k < segments[1].start_24k
    assert all(2.0 <= segment.seconds <= 12.0 for segment in segments)
    assert sum(segment.seconds for segment in segments) >= 5.0
    # louder second burst must rank higher
    assert segments[1].rms > segments[0].rms


def test_slice_speech_caps_total_seconds(tmp_path):
    from voice.clone_pipeline import slice_speech
    from voice.vad.energy import EnergyVAD

    rate = 16000
    bursts = [(i * 2.0, 1.0, 0.5) for i in range(20)]  # 20s of speech spread over ~40s
    audio = speech_like(41.0, rate, bursts)
    path = tmp_path / "speech.wav"
    path.write_bytes(mono_wav_bytes(audio, rate))

    segments = slice_speech(path, EnergyVAD(), min_segment=0.5, max_segment=12.0, min_total=2.0, target_seconds=10.0)
    assert sum(segment.speech_seconds for segment in segments) <= 10.0 + 0.5
    assert sum(segment.speech_seconds for segment in segments) >= 9.0


def test_slice_speech_raises_when_clean_speech_too_short(tmp_path):
    from voice.clone_pipeline import ClonePipelineError, slice_speech
    from voice.vad.energy import EnergyVAD

    rate = 16000
    audio = speech_like(5.0, rate, [(1.0, 1.0, 0.5)])
    path = tmp_path / "speech.wav"
    path.write_bytes(mono_wav_bytes(audio, rate))
    with pytest.raises(ClonePipelineError):
        slice_speech(path, EnergyVAD(), min_segment=2.0, max_segment=12.0, min_total=5.0)


def test_slice_speech_merges_segments_across_short_gaps(tmp_path):
    from voice.clone_pipeline import slice_speech
    from voice.vad.energy import EnergyVAD

    rate = 16000
    # two 3s bursts separated by a 1s gap (below default merge_gap 1.5s)
    audio = speech_like(8.0, rate, [(1.0, 3.0, 0.5), (5.0, 3.0, 0.5)])
    path = tmp_path / "speech.wav"
    path.write_bytes(mono_wav_bytes(audio, rate))
    segments = slice_speech(path, EnergyVAD(), min_segment=4.0, max_segment=12.0, min_total=4.0)
    assert len(segments) == 1
    assert segments[0].seconds == pytest.approx(7.0, abs=0.5)


def test_build_reference_concatenates_chosen_segments(tmp_path):
    from voice.clone_pipeline import Segment, build_reference

    rate = 24000
    audio = speech_like(10.0, rate, [(0.0, 4.0, 0.5), (5.0, 5.0, 0.7)])
    source = tmp_path / "vocals24k.wav"
    source.write_bytes(mono_wav_bytes(audio, rate))
    segments = [
        Segment(start_24k=0, end_24k=4 * rate, seconds=4.0, speech_seconds=4.0, rms=0.3),
        Segment(start_24k=5 * rate, end_24k=10 * rate, seconds=5.0, speech_seconds=5.0, rms=0.5),
    ]
    target = tmp_path / "reference.wav"
    build_reference(source, segments, target, max_seconds=20.0)
    with wave.open(str(target), "rb") as output:
        assert output.getframerate() == rate
        assert output.getnchannels() == 1
        assert output.getsampwidth() == 2
        assert output.getnframes() == 9 * rate


def test_build_reference_limits_total_duration(tmp_path):
    from voice.clone_pipeline import Segment, build_reference

    rate = 24000
    audio = speech_like(30.0, rate, [(0.0, 30.0, 0.5)])
    source = tmp_path / "vocals24k.wav"
    source.write_bytes(mono_wav_bytes(audio, rate))
    segments = [Segment(start_24k=0, end_24k=25 * rate, seconds=25.0, speech_seconds=25.0, rms=0.5)]
    target = tmp_path / "reference.wav"
    build_reference(source, segments, target, max_seconds=20.0)
    with wave.open(str(target), "rb") as output:
        assert output.getnframes() == 20 * rate


def test_extract_audio_produces_44100_stereo(tmp_path):
    from voice.clone_pipeline import extract_audio, find_ffmpeg

    ffmpeg = find_ffmpeg(Path.cwd())
    video = tmp_path / "sample.mp4"
    audio = tmp_path / "audio.wav"
    audio.write_bytes(mono_wav_bytes(speech_like(8.0, 16000, [(1.0, 2.0, 0.5)]), 16000))
    subprocess_run(ffmpeg, video, audio)
    target = tmp_path / "audio.wav"
    extract_audio(ffmpeg, video, target)
    with wave.open(str(target), "rb") as output:
        assert output.getframerate() == 44100
        assert output.getnchannels() == 2
        assert output.getnframes() > 0


def subprocess_run(ffmpeg: Path, video: Path, audio: Path):
    import subprocess

    subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=18:size=128x72:rate=10",
            "-i",
            str(audio),
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        capture_output=True,
    )


def test_run_pipeline_builds_reference(tmp_path):
    from voice.clone_pipeline import find_ffmpeg, run_clone_pipeline
    from voice.separator.onnx import HtdemucsSeparator
    from voice.vad.energy import EnergyVAD

    class PassVocalsSeparator:
        """Fake separator: vocals = input mix (identity)."""

        def separate(self, input_wav, output_wav, progress=None):
            with wave.open(str(input_wav), "rb") as source:
                rate = source.getframerate()
                channels = source.getnchannels()
                frames = source.readframes(source.getnframes())
            with wave.open(str(output_wav), "wb") as target:
                target.setnchannels(channels)
                target.setsampwidth(2)
                target.setframerate(rate)
                target.writeframes(frames)

    ffmpeg = find_ffmpeg(Path.cwd())
    video = tmp_path / "sample.mp4"
    audio = tmp_path / "bursts.wav"
    audio.write_bytes(mono_wav_bytes(speech_like(18.0, 16000, [(1.0, 5.0, 0.5), (9.0, 8.0, 0.8)]), 16000))
    subprocess_run(ffmpeg, video, audio)
    phases: list[str] = []
    result = run_clone_pipeline(
        tmp_path,
        video,
        ffmpeg=ffmpeg,
        separator=PassVocalsSeparator(),
        vad_factory=lambda: EnergyVAD(),
        on_progress=lambda phase, percent: phases.append(phase),
    )
    assert phases[0] == "extract"
    assert phases[-1] == "build"
    assert result["reference_path"].is_file()
    with wave.open(str(result["reference_path"]), "rb") as output:
        assert output.getframerate() == 24000
        assert output.getnchannels() == 1
        assert 0 < output.getnframes() <= 20 * 24000
    assert result["duration_seconds"] > 0
