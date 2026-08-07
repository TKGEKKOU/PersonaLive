"""Video -> clean reference voice pipeline (方案 A).

video -> ffmpeg 44.1k stereo -> htdemucs vocals -> 16k mono for VAD slicing
-> 24k mono reference (clean speech segments concatenated, capped at 20 s).
"""

from __future__ import annotations

import io
import shutil
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from voice.vad.base import VAD

REFERENCE_RATE = 24000
VAD_RATE = 16000
# 官方推荐 10~20s；30s 为试验上限（超过 30s 反而可能降低相似度）。
MAX_REFERENCE_SECONDS = 30.0
# GPT-SoVITS training wants as much clean speech as possible; slicing keeps
# up to 10 minutes of speech instead of capping at the reference length.
SLICE_TARGET_SECONDS = 600.0


class ClonePipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class Segment:
    """One clean speech slice, positioned on the 24 kHz timeline."""

    start_24k: int
    end_24k: int
    seconds: float  # span seconds (including short pauses inside the slice)
    speech_seconds: float  # actual speech seconds inside the span
    rms: float


@dataclass(frozen=True)
class SegmentFile:
    """One persisted clean-speech slice plus its on-disk wav files."""

    index: int
    seconds: float
    rms: float
    start_24k: int
    end_24k: int
    wav_24k: Path

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "seconds": round(self.seconds, 2),
            "rms": round(self.rms, 4),
            "start_24k": self.start_24k,
            "end_24k": self.end_24k,
            "file": self.wav_24k.name,
            "source": "auto",
        }


def find_ffmpeg(project_root: Path) -> Path:
    managed = Path(project_root) / "runtime" / "ffmpeg" / "ffmpeg.exe"
    if managed.is_file():
        return managed
    located = shutil.which("ffmpeg")
    if located:
        return Path(located)
    raise ClonePipelineError("未找到 ffmpeg，无法提取视频音轨")


def _run_ffmpeg(ffmpeg: Path, args: list[str]) -> None:
    command = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", *args]
    try:
        subprocess.run(command, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf-8", errors="replace")[-500:]
        raise ClonePipelineError(f"ffmpeg 处理失败：{detail.strip()}") from exc


def extract_audio(ffmpeg: Path, video: Path, output_wav: Path) -> Path:
    """Extract the soundtrack as a 44.1 kHz stereo PCM WAV for separation."""
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        ffmpeg,
        ["-i", str(video), "-vn", "-ac", "2", "-ar", str(44100), "-c:a", "pcm_s16le", str(output_wav)],
    )
    return output_wav


def convert_wav(ffmpeg: Path, source: Path, target: Path, rate: int, channels: int = 1) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        ffmpeg,
        ["-i", str(source), "-ac", str(channels), "-ar", str(rate), "-c:a", "pcm_s16le", str(target)],
    )
    return target


def _read_mono(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        channels = source.getnchannels()
        if channels != 1 or source.getsampwidth() != 2:
            raise ClonePipelineError("内部音频格式错误（应为单声道 16-bit WAV）")
        raw = source.readframes(source.getnframes())
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    return samples, rate


def _write_mono(path: Path, samples: np.ndarray, rate: int) -> None:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(pcm.tobytes())


def _normalize_loudness(samples: np.ndarray, target_rms: float = 0.15, max_gain: float = 10.0) -> np.ndarray:
    """Amplify quiet separated vocals to a healthy level before slicing.

    htdemucs output is often quieter than the source mix; a fixed VAD threshold
    would otherwise miss soft speech. Cap the gain so near-silence is not
    amplified into fake speech.
    """
    rms = float(np.sqrt(np.mean(np.square(samples), dtype=np.float64)))
    if rms <= 0:
        return samples
    gain = min(max_gain, target_rms / rms)
    return np.clip(samples * gain, -1.0, 1.0).astype(np.float32)


def slice_speech(
    wav_16k: Path,
    vad: VAD,
    target_seconds: float = MAX_REFERENCE_SECONDS,
    min_segment: float = 1.0,
    max_segment: float = 12.0,
    min_total: float = 5.0,
    merge_gap_seconds: float = 1.5,
) -> list[Segment]:
    """Split clean speech into ranked segments, capped at target_seconds.

    Returns chronological segments mapped to the 24 kHz timeline, ordered by
    their position in the source. Raises ClonePipelineError when the usable
    speech is shorter than min_total.
    """
    samples, rate = _read_mono(wav_16k)
    if rate != VAD_RATE:
        raise ClonePipelineError(f"切片输入应为 {VAD_RATE} Hz，实际 {rate} Hz")
    samples = _normalize_loudness(samples)
    vad.reset()
    boundaries: list[tuple[str, int]] = []
    frame = max(1, int(rate * 0.25))
    for start in range(0, len(samples), frame):
        for event in vad.process(samples[start : start + frame]):
            boundaries.append((event.kind, event.sample_index))
    if not boundaries or boundaries[0][0] != "speech_start":
        raise ClonePipelineError("未在视频音轨中检测到语音")

    raw_segments: list[tuple[int, int, float]] = []
    open_start: int | None = None
    for kind, sample_index in boundaries:
        if kind == "speech_start" and open_start is None:
            open_start = sample_index
        elif kind == "speech_stop" and open_start is not None:
            raw_segments.append((open_start, sample_index, 0.0))
            open_start = None
    if open_start is not None:
        raw_segments.append((open_start, len(samples), 0.0))

    merged: list[tuple[int, int, float]] = []
    for start, stop, _ in raw_segments:
        within_gap = merged and (start - merged[-1][1]) / rate <= merge_gap_seconds
        within_cap = merged and (stop - merged[-1][0]) / rate <= max_segment
        if within_gap and within_cap:
            previous_start, previous_stop, previous_speech = merged[-1]
            merged[-1] = (previous_start, stop, previous_speech + (stop - start))
        else:
            merged.append((start, stop, stop - start))

    candidates: list[Segment] = []
    for start, stop, speech_samples in merged:
        duration = (stop - start) / rate
        if duration < min_segment or duration > max_segment:
            continue
        chunk = samples[start:stop]
        rms = float(np.sqrt(np.mean(np.square(chunk), dtype=np.float64)))
        candidates.append(
            Segment(
                start_24k=int(start * REFERENCE_RATE / rate),
                end_24k=int(stop * REFERENCE_RATE / rate),
                seconds=duration,
                speech_seconds=speech_samples / rate,
                rms=rms,
            )
        )

    ranked = sorted(candidates, key=lambda segment: segment.rms, reverse=True)
    chosen: list[Segment] = []
    total = 0.0
    for segment in ranked:
        if segment.speech_seconds <= target_seconds - total:
            chosen.append(segment)
            total += segment.speech_seconds
            continue
        # 最后一段只取能放进预算的部分（按语音时长比例裁剪跨度）
        fraction = max(0.0, (target_seconds - total) / segment.speech_seconds)
        if fraction > 0.05:
            trimmed_end = segment.start_24k + int((segment.end_24k - segment.start_24k) * fraction)
            chosen.append(
                Segment(
                    start_24k=segment.start_24k,
                    end_24k=trimmed_end,
                    seconds=(trimmed_end - segment.start_24k) / REFERENCE_RATE,
                    speech_seconds=target_seconds - total,
                    rms=segment.rms,
                )
            )
            total = target_seconds
        break
    if total < min_total:
        raise ClonePipelineError(f"可用的干净语音不足 {min_total:.0f} 秒（当前 {total:.1f} 秒），请换一段说话更清晰、背景更安静的视频")
    return sorted(chosen, key=lambda segment: segment.start_24k)


def build_reference(wav_24k: Path, segments: list[Segment], output_wav: Path, max_seconds: float = MAX_REFERENCE_SECONDS) -> Path:
    """Concatenate the chosen segments into one reference WAV."""
    samples, rate = _read_mono(wav_24k)
    if rate != REFERENCE_RATE:
        raise ClonePipelineError(f"参考音频应为 {REFERENCE_RATE} Hz，实际 {rate} Hz")
    pieces: list[np.ndarray] = []
    total = 0.0
    for segment in segments:
        if total + segment.seconds > max_seconds:
            if not pieces and segment.seconds > max_seconds:
                hi = segment.start_24k + int(max_seconds * rate)
                if hi <= len(samples):
                    pieces.append(samples[segment.start_24k:hi])
                    total = max_seconds
            break
        lo = max(0, min(segment.start_24k, len(samples)))
        hi = max(lo, min(segment.end_24k, len(samples)))
        if hi > lo:
            pieces.append(samples[lo:hi])
            total += (hi - lo) / rate
    if not pieces:
        raise ClonePipelineError("没有可用的语音片段用于生成参考音色")
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    _write_mono(output_wav, np.concatenate(pieces), REFERENCE_RATE)
    return output_wav


def run_audio_to_segments(
    audio_wav: Path,
    task_dir: Path,
    ffmpeg: Path,
    separator: object,
    vad_factory: Callable[[], VAD],
    on_progress: Callable[[str, int], None] | None = None,
) -> dict:
    """Separate a 44.1k stereo wav into persisted clean-speech segment files.

    Returns metadata dict with ``segments`` (list of SegmentFile.to_dict()),
    plus intermediate wav paths for preview/debug.
    """
    task_dir = Path(task_dir)
    work = task_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    segments_dir = task_dir / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    def report(phase: str, percent: int) -> None:
        if on_progress is not None:
            on_progress(phase, percent)

    report("separate", 5)
    vocals_44k = work / "vocals_44k.wav"
    separator.separate(audio_wav, vocals_44k, progress=lambda done, total: report("separate", 5 + int(50 * done / max(1, total))))
    report("convert", 60)
    vocals_16k = convert_wav(ffmpeg, vocals_44k, work / "vocals_16k.wav", VAD_RATE, 1)
    vocals_24k = convert_wav(ffmpeg, vocals_44k, work / "vocals_24k.wav", REFERENCE_RATE, 1)
    report("slice", 75)
    segments = slice_speech(vocals_16k, vad_factory(), target_seconds=SLICE_TARGET_SECONDS)
    report("write", 90)
    with wave.open(str(vocals_24k), "rb") as source:
        rate = source.getframerate()
        raw_24k = np.frombuffer(source.readframes(source.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    # 与 16k 切片轨道一致做整轨响度归一化，保证落盘片段与显示的 RMS 一致。
    normalized_24k = _normalize_loudness(raw_24k)
    segment_files: list[SegmentFile] = []
    for index, segment in enumerate(segments):
        target = segments_dir / f"segment_{index + 1:03d}.wav"
        piece = normalized_24k[segment.start_24k : segment.end_24k]
        rms = float(np.sqrt(np.mean(np.square(piece), dtype=np.float64)))
        _write_mono(target, piece, rate)
        segment_files.append(
            SegmentFile(
                index=index,
                seconds=segment.speech_seconds,
                rms=rms,
                start_24k=segment.start_24k,
                end_24k=segment.end_24k,
                wav_24k=target,
            )
        )
    report("slice", 100)
    return {
        "vocals_44k": vocals_44k,
        "vocals_16k": vocals_16k,
        "vocals_24k": vocals_24k,
        "segments": [item.to_dict() for item in segment_files],
    }


def run_video_to_segments(
    video_path: Path,
    task_dir: Path,
    ffmpeg: Path,
    separator: object,
    vad_factory: Callable[[], VAD],
    on_progress: Callable[[str, int], None] | None = None,
) -> dict:
    """Extract audio from a video, then run audio -> segments."""
    task_dir = Path(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    work = task_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    def report(phase: str, percent: int) -> None:
        if on_progress is not None:
            on_progress(phase, percent)

    report("extract", 2)
    audio = extract_audio(ffmpeg, video_path, work / "audio_44k.wav")
    result = run_audio_to_segments(
        audio,
        task_dir,
        ffmpeg=ffmpeg,
        separator=separator,
        vad_factory=vad_factory,
        on_progress=lambda phase, percent: report(phase, 2 + int(percent * 0.98)),
    )
    result["audio_44k"] = audio
    return result


def build_reference_from_segments(
    segments: list[dict],
    segments_dir: Path,
    indices: list[int],
    output_wav: Path,
    max_seconds: float = MAX_REFERENCE_SECONDS,
) -> Path:
    """Concatenate chosen segment files (24k mono) into one reference wav."""
    by_index = {item["index"]: item for item in segments}
    pieces: list[bytes] = []
    total = 0.0
    for raw_index in indices:
        item = by_index.get(raw_index)
        if item is None or item["seconds"] <= 0:
            continue
        if total + item["seconds"] > max_seconds:
            break
        path = Path(segments_dir) / item["file"]
        if not path.is_file():
            continue
        pieces.append(path.read_bytes())
        total += item["seconds"]
    if not pieces:
        raise ClonePipelineError("请至少选择一条干净语音片段")
    output_wav.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    rate = REFERENCE_RATE
    for payload in pieces:
        with wave.open(io.BytesIO(payload), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise ClonePipelineError("片段格式异常")
            rate = source.getframerate()
            frames.extend(source.readframes(source.getnframes()))
    with wave.open(str(output_wav), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(bytes(frames))
    return output_wav


def run_clone_pipeline(
    task_dir: Path,
    video_path: Path,
    ffmpeg: Path,
    separator: object,
    vad_factory: Callable[[], VAD],
    on_progress: Callable[[str, int], None] | None = None,
) -> dict:
    """Run the full pipeline inside task_dir and return the reference file."""
    task_dir = Path(task_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    work = task_dir / "work"
    work.mkdir(parents=True, exist_ok=True)

    def report(phase: str, percent: int) -> None:
        if on_progress is not None:
            on_progress(phase, percent)

    result = run_video_to_segments(
        video_path,
        task_dir,
        ffmpeg=ffmpeg,
        separator=separator,
        vad_factory=vad_factory,
        on_progress=report,
    )
    segments = [
        Segment(
            start_24k=item["start_24k"],
            end_24k=item["end_24k"],
            seconds=item["seconds"],
            speech_seconds=item["seconds"],
            rms=item["rms"],
        )
        for item in result["segments"]
    ]
    report("build", 95)
    reference = build_reference_from_segments(
        result["segments"],
        task_dir / "segments",
        [item["index"] for item in result["segments"]],
        task_dir / "reference.wav",
    )
    report("build", 100)
    return {
        "reference_path": reference,
        "duration_seconds": round(sum(segment.seconds for segment in segments), 1),
        "segment_count": len(segments),
    }
