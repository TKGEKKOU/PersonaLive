"""Voice studio: stepwise draft pipeline for building named reference voices."""

from __future__ import annotations

import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from voice.clone_pipeline import (
    ClonePipelineError,
    REFERENCE_RATE,
    build_reference_from_segments,
    convert_wav,
    find_ffmpeg,
    run_audio_to_segments,
    run_video_to_segments,
)

SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
VOICE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class VoiceStudioError(RuntimeError):
    pass


def _empty_meta(session_id: str) -> dict:
    return {
        "session_id": session_id,
        "created_at": time.time(),
        "updated_at": time.time(),
        "phase": "idle",
        "progress": 0,
        "error": "",
        "source_kind": None,
        "source_name": "",
        "audio_files": [],
        "source_duration": None,
        "segments": [],
        "selected": [],
        "reference_file": None,
        "reference_seconds": None,
        "reference_source": None,
        "voice_id": None,
        "voice_name": None,
    }


class VoiceStudioManager:
    def __init__(
        self,
        project_root: Path,
        separator_factory: Callable[[], object],
        vad_factory: Callable,
        voices_root: Path | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.sessions_dir = self.project_root / "data" / "voice_studio" / "sessions"
        self.meta_dir = self.project_root / "data" / "voice_studio" / "voices"
        self.voices_root = Path(voices_root) if voices_root else self.project_root / "data" / "tts" / "voices"
        self.separator_factory = separator_factory
        self.vad_factory = vad_factory
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------

    def create_session(self) -> dict:
        session_id = uuid.uuid4().hex[:12]
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "work").mkdir(exist_ok=True)
        (session_dir / "uploads").mkdir(exist_ok=True)
        self._save_meta(session_id, _empty_meta(session_id))
        return self.session_state(session_id)

    def _session_dir(self, session_id: str) -> Path:
        if not SESSION_ID_RE.match(session_id):
            raise VoiceStudioError("无效的会话 ID")
        return self.sessions_dir / session_id

    def _meta_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "meta.json"

    def _load_meta(self, session_id: str) -> dict:
        path = self._meta_path(session_id)
        if not path.is_file():
            raise VoiceStudioError("会话不存在或已过期")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise VoiceStudioError("会话数据损坏") from exc

    def _save_meta(self, session_id: str, meta: dict) -> None:
        meta["updated_at"] = time.time()
        path = self._meta_path(session_id)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def session_state(self, session_id: str) -> dict | None:
        try:
            meta = self._load_meta(session_id)
        except VoiceStudioError:
            return None
        meta = dict(meta)
        meta["running"] = session_id in self._cancel and self._cancel[session_id].is_set() is False
        return meta

    def list_sessions(self) -> list[dict]:
        sessions: list[dict] = []
        if not self.sessions_dir.is_dir():
            return sessions
        for entry in sorted(self.sessions_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not entry.is_dir() or not (entry / "meta.json").is_file():
                continue
            state = self.session_state(entry.name)
            if state is not None:
                sessions.append(
                    {
                        "session_id": state["session_id"],
                        "phase": state["phase"],
                        "progress": state["progress"],
                        "source_kind": state["source_kind"],
                        "source_name": state["source_name"],
                        "updated_at": state["updated_at"],
                    }
                )
        return sessions

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            cancel = self._cancel.pop(session_id, None)
            if cancel is not None:
                cancel.set()
        directory = self._session_dir(session_id)
        if directory.is_dir():
            shutil.rmtree(directory)
            return True
        return False

    # ------------------------------------------------------------------
    # pipeline tasks
    # ------------------------------------------------------------------

    def start_video_task(self, session_id: str, video_path: Path) -> dict:
        meta = self._load_meta(session_id)
        meta.update({"phase": "queued", "progress": 0, "error": "", "source_kind": "video", "source_name": Path(video_path).name})
        self._save_meta(session_id, meta)
        self._spawn(session_id, lambda cancel: self._run_video(session_id, video_path, cancel))
        return self.session_state(session_id)

    def upload_audio_files(self, session_id: str, audio_paths: list[Path]) -> dict:
        meta = self._load_meta(session_id)
        meta.update(
            {
                "phase": "convert",
                "progress": 0,
                "error": "",
                "source_kind": "audio",
                "source_name": "；".join(Path(path).name for path in audio_paths[:2]) + (f" 等 {len(audio_paths)} 个" if len(audio_paths) > 2 else ""),
                "audio_files": [],
                "segments": [],
                "selected": [],
                "reference_file": None,
                "reference_seconds": None,
            }
        )
        self._save_meta(session_id, meta)
        self._spawn(session_id, lambda cancel: self._convert_audio_files(session_id, list(audio_paths), cancel))
        return self.session_state(session_id)

    def start_separation(self, session_id: str) -> dict:
        meta = self._load_meta(session_id)
        audio_wav = self._session_dir(session_id) / "work" / "audio_44k.wav"
        if not audio_wav.is_file():
            raise VoiceStudioError("请先上传音频")
        if session_id in self._cancel and not self._cancel[session_id].is_set():
            raise VoiceStudioError("该草稿已有任务进行中")
        self._update_meta(session_id, {"phase": "queued", "progress": 0, "error": ""})
        self._spawn(session_id, lambda cancel: self._run_separation(session_id, audio_wav, cancel))
        return self.session_state(session_id)

    def _spawn(self, session_id: str, target: Callable[[threading.Event], None]) -> None:
        with self._lock:
            if session_id in self._cancel and not self._cancel[session_id].is_set():
                raise VoiceStudioError("该草稿已有任务进行中")
            cancel = threading.Event()
            self._cancel[session_id] = cancel
        threading.Thread(target=self._guard, args=(session_id, cancel, target), daemon=True, name=f"voice-studio-{session_id}").start()

    def _guard(self, session_id: str, cancel: threading.Event, target: Callable[[threading.Event], None]) -> None:
        try:
            target(cancel)
        except RuntimeError as exc:
            if cancel.is_set():
                self._update_meta(session_id, {"phase": "cancelled", "error": ""})
            else:
                self._update_meta(session_id, {"phase": "failed", "error": str(exc)})
        except (OSError, ClonePipelineError, VoiceStudioError) as exc:
            self._update_meta(session_id, {"phase": "failed", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 后台任务兜底
            self._update_meta(session_id, {"phase": "failed", "error": str(exc)})
        finally:
            with self._lock:
                self._cancel.pop(session_id, None)

    def _update_meta(self, session_id: str, changes: dict) -> None:
        with self._lock:
            meta = self._load_meta(session_id)
            meta.update(changes)
            self._save_meta(session_id, meta)

    def _run_video(self, session_id: str, video_path: Path, cancel: threading.Event) -> None:
        session_dir = self._session_dir(session_id)
        ffmpeg = find_ffmpeg(self.project_root)

        def report(phase: str, percent: int) -> None:
            if cancel.is_set():
                raise RuntimeError("任务已取消")
            self._update_meta(session_id, {"phase": phase, "progress": percent})

        result = run_video_to_segments(
            video_path,
            session_dir,
            ffmpeg=ffmpeg,
            separator=self.separator_factory(),
            vad_factory=self.vad_factory,
            on_progress=report,
        )
        self._store_segments(session_id, result)

    def _convert_audio_files(self, session_id: str, audio_paths: list[Path], cancel: threading.Event) -> None:
        session_dir = self._session_dir(session_id)
        ffmpeg = find_ffmpeg(self.project_root)
        work = session_dir / "work"
        work.mkdir(parents=True, exist_ok=True)
        converted: list[Path] = []
        files_meta: list[dict] = []
        for index, audio_path in enumerate(audio_paths):
            if cancel.is_set():
                raise RuntimeError("任务已取消")
            target = work / f"audio_{index + 1}.wav"
            convert_wav(ffmpeg, audio_path, target, 44100, 2)
            files_meta.append({"name": Path(audio_path).name, "seconds": wave_open_duration(target)})
            converted.append(target)
            self._update_meta(session_id, {"phase": "convert", "progress": int(20 + 70 * (index + 1) / len(audio_paths))})
        if not converted:
            raise VoiceStudioError("没有可用的音频文件")
        audio_wav = work / "audio_44k.wav"
        if len(converted) == 1:
            shutil.copy2(converted[0], audio_wav)
        else:
            inputs: list[str] = []
            for path in converted:
                inputs.extend(["-i", str(path)])
            filter_parts = [f"[{i}:a]" for i in range(len(converted))]
            filter_expr = f"{''.join(filter_parts)}concat=n={len(converted)}:v=0:a=1[a]"
            _run_ffmpeg_capture(
                ffmpeg,
                [*inputs, "-filter_complex", filter_expr, "-map", "[a]", "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(audio_wav)],
            )
        self._update_meta(
            session_id,
            {
                "phase": "audio_ready",
                "progress": 100,
                "audio_files": files_meta,
                "source_duration": wave_open_duration(audio_wav),
            },
        )

    def _run_separation(self, session_id: str, audio_wav: Path, cancel: threading.Event) -> None:
        session_dir = self._session_dir(session_id)
        ffmpeg = find_ffmpeg(self.project_root)

        def report(phase: str, percent: int) -> None:
            if cancel.is_set():
                raise RuntimeError("任务已取消")
            self._update_meta(session_id, {"phase": phase, "progress": percent})

        result = run_audio_to_segments(
            audio_wav,
            session_dir,
            ffmpeg=ffmpeg,
            separator=self.separator_factory(),
            vad_factory=self.vad_factory,
            on_progress=report,
        )
        result["audio_44k"] = audio_wav
        self._store_segments(session_id, result)

    def upload_segments(self, session_id: str, audio_paths: list[Path]) -> dict:
        """Accept user-uploaded clean clips as extra reference segments."""
        session_dir = self._session_dir(session_id)
        ffmpeg = find_ffmpeg(self.project_root)
        segments_dir = session_dir / "segments"
        segments_dir.mkdir(parents=True, exist_ok=True)
        meta = self._load_meta(session_id)
        next_index = max((segment["index"] for segment in meta["segments"]), default=-1) + 1
        added: list[dict] = []
        for offset, audio_path in enumerate(audio_paths):
            target = segments_dir / f"upload_{uuid.uuid4().hex[:8]}.wav"
            converted = segments_dir / f"upload_{uuid.uuid4().hex[:8]}.convert.wav"
            convert_wav(ffmpeg, audio_path, converted, REFERENCE_RATE, 1)
            seconds = wav_seconds(converted)
            if seconds > 30.0:
                _run_ffmpeg_capture(ffmpeg, ["-i", str(converted), "-t", "30", "-c:a", "pcm_s16le", str(target)])
                seconds = 30.0
            else:
                shutil.copy2(converted, target)
            converted.unlink(missing_ok=True)
            added.append(
                {
                    "index": next_index + offset,
                    "seconds": round(seconds, 2),
                    "rms": 0.0,
                    "start_24k": 0,
                    "end_24k": int(REFERENCE_RATE * seconds),
                    "file": target.name,
                    "source": "upload",
                }
            )
        if not added:
            raise VoiceStudioError("没有可用的音频片段")
        meta["segments"] = [*meta["segments"], *added]
        if not meta.get("reference_file"):
            meta["phase"] = "segments"
            meta["progress"] = 100
        self._save_meta(session_id, meta)
        return self.session_state(session_id)

    def delete_segment(self, session_id: str, index: int) -> bool:
        """Remove a user-uploaded segment; auto segments are kept."""
        meta = self._load_meta(session_id)
        item = next((segment for segment in meta["segments"] if segment["index"] == index), None)
        if item is None or item.get("source") != "upload":
            return False
        path = self._session_dir(session_id) / "segments" / item["file"]
        path.unlink(missing_ok=True)
        meta["segments"] = [segment for segment in meta["segments"] if segment["index"] != index]
        meta["selected"] = [value for value in meta["selected"] if value != index]
        self._save_meta(session_id, meta)
        return True

    def _run_audio(self, session_id: str, audio_path: Path, cancel: threading.Event) -> None:
        audio_wav = convert_wav(find_ffmpeg(self.project_root), audio_path, self._session_dir(session_id) / "work" / "audio_44k.wav", 44100, 2)
        self._run_separation(session_id, audio_wav, cancel)

    def _store_segments(self, session_id: str, result: dict) -> None:
        segments = result["segments"]
        if not segments:
            raise ClonePipelineError("未能截取到可用的干净语音片段，请换一段说话更清晰、背景更安静的视频或音频")
        source_duration = wave_open_duration(result["audio_44k"])
        self._update_meta(
            session_id,
            {
                "phase": "segments",
                "progress": 100,
                "segments": segments,
                "selected": [],
                "reference_file": None,
                "reference_seconds": None,
                "source_duration": source_duration,
            },
        )

    # ------------------------------------------------------------------
    # segments / reference
    # ------------------------------------------------------------------

    def segment_path(self, session_id: str, index: int) -> Path | None:
        meta = self._load_meta(session_id)
        item = next((segment for segment in meta["segments"] if segment["index"] == index), None)
        if item is None:
            return None
        path = self._session_dir(session_id) / "segments" / item["file"]
        return path if path.is_file() else None

    def select_segments(self, session_id: str, indices: list[int]) -> dict:
        meta = self._load_meta(session_id)
        if not meta["segments"]:
            raise VoiceStudioError("没有可用片段，请先完成音频处理")
        reference = build_reference_from_segments(
            meta["segments"],
            self._session_dir(session_id) / "segments",
            [int(index) for index in indices],
            self._session_dir(session_id) / "reference.wav",
        )
        seconds = wave_open_duration(reference)
        self._update_meta(
            session_id,
            {
                "selected": [int(index) for index in indices],
                "reference_file": reference.name,
                "reference_seconds": seconds,
                "reference_source": "segments",
                "phase": "reference",
            },
        )
        return self.session_state(session_id)

    def upload_reference(self, session_id: str, audio_path: Path) -> dict:
        """Accept a directly-uploaded clean audio clip as the reference."""
        session_dir = self._session_dir(session_id)
        ffmpeg = find_ffmpeg(self.project_root)
        converted = session_dir / "work" / "reference_upload.wav"
        convert_wav(ffmpeg, audio_path, converted, REFERENCE_RATE, 1)
        seconds = wav_seconds(converted)
        if seconds > 30.0:
            trimmed = session_dir / "reference.wav"
            _run_ffmpeg_capture(ffmpeg, ["-i", str(converted), "-t", "30", "-c:a", "pcm_s16le", str(trimmed)])
            seconds = 30.0
        else:
            trimmed = session_dir / "reference.wav"
            shutil.copy2(converted, trimmed)
        self._update_meta(
            session_id,
            {
                "selected": [],
                "reference_file": trimmed.name,
                "reference_seconds": round(seconds, 1),
                "reference_source": "upload",
                "phase": "reference",
                "progress": 100,
            },
        )
        return self.session_state(session_id)

    def reference_path(self, session_id: str) -> Path | None:
        meta = self._load_meta(session_id)
        if not meta.get("reference_file"):
            return None
        path = self._session_dir(session_id) / meta["reference_file"]
        return path if path.is_file() else None

    # ------------------------------------------------------------------
    # named voices
    # ------------------------------------------------------------------

    def complete_session(self, session_id: str, name: str) -> dict:
        meta = self._load_meta(session_id)
        reference = self.reference_path(session_id)
        if reference is None:
            raise VoiceStudioError("尚未生成参考音色")
        name = name.strip()
        if not name:
            raise VoiceStudioError("请为音色命名")
        voice_id = uuid.uuid4().hex[:12]
        self.voices_root.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        target = self.voices_root / f"{voice_id}.wav"
        shutil.copy2(reference, target)
        info = {
            "voice_id": voice_id,
            "name": name,
            "created_at": time.time(),
            "duration_seconds": meta.get("reference_seconds"),
            "segment_count": len(meta.get("selected") or []),
            "reference_source": meta.get("reference_source"),
            "session_id": session_id,
        }
        (self.meta_dir / f"{voice_id}.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        self._update_meta(session_id, {"phase": "done", "progress": 100, "voice_id": voice_id, "voice_name": name})
        return self.list_voices_by_id(voice_id)

    def list_voices(self) -> list[dict]:
        voices: list[dict] = []
        if not self.meta_dir.is_dir():
            return voices
        for path in sorted(self.meta_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                info = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (self.voices_root / f"{info.get('voice_id')}.wav").is_file():
                voices.append(info)
        return voices

    def list_voices_by_id(self, voice_id: str) -> dict:
        for voice in self.list_voices():
            if voice["voice_id"] == voice_id:
                return voice
        raise VoiceStudioError("音色不存在")

    def voice_path(self, voice_id: str) -> Path | None:
        if not VOICE_ID_RE.match(voice_id):
            return None
        path = self.voices_root / f"{voice_id}.wav"
        return path if path.is_file() else None

    def delete_voice(self, voice_id: str) -> bool:
        if not VOICE_ID_RE.match(voice_id):
            return False
        deleted = False
        wav = self.voices_root / f"{voice_id}.wav"
        if wav.is_file():
            wav.unlink()
            deleted = True
        meta = self.meta_dir / f"{voice_id}.json"
        if meta.is_file():
            meta.unlink()
            deleted = True
        return deleted


def wave_open_duration(path: Path) -> float:
    import wave

    with wave.open(str(path), "rb") as source:
        return round(source.getnframes() / float(source.getframerate()), 2)


def wav_seconds(path: Path) -> float:
    return wave_open_duration(path)


def _run_ffmpeg_capture(ffmpeg: Path, args: list[str]) -> None:
    import subprocess

    subprocess.run(
        [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", *args],
        check=True,
        capture_output=True,
    )
