"""In-memory background task runner for video -> reference voice imports."""

from __future__ import annotations

import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from voice.clone_pipeline import find_ffmpeg, run_clone_pipeline


class CloneTaskManager:
    def __init__(
        self,
        project_root: Path,
        separator_factory: Callable[[], object],
        vad_factory: Callable,
    ) -> None:
        self.project_root = Path(project_root)
        self.tasks_dir = self.project_root / "data" / "video_clone"
        self.separator_factory = separator_factory
        self.vad_factory = vad_factory
        self._tasks: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start(self, video_path: Path, apply_result: Callable[[Path], None]) -> str:
        task_id = uuid.uuid4().hex[:12]
        task_dir = self.tasks_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "task_id": task_id,
            "task_dir": task_dir,
            "video_path": Path(video_path),
            "state": "running",
            "phase": "queued",
            "progress": 0,
            "error": "",
            "duration_seconds": None,
            "segment_count": None,
            "cancel_event": threading.Event(),
            "created_at": time.time(),
        }
        with self._lock:
            self._tasks[task_id] = record
        threading.Thread(
            target=self._run,
            args=(record, apply_result),
            daemon=True,
            name=f"video-clone-{task_id}",
        ).start()
        return task_id

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            record = self._tasks.get(task_id)
            return dict(record) if record else None

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            record = self._tasks.get(task_id)
        if record is None or record["state"] != "running":
            return False
        record["cancel_event"].set()
        return True

    def cleanup(self, task_id: str) -> None:
        with self._lock:
            record = self._tasks.pop(task_id, None)
        if record is not None:
            shutil.rmtree(record["task_dir"], ignore_errors=True)

    def _run(self, record: dict, apply_result: Callable[[Path], None]) -> None:
        task_dir: Path = record["task_dir"]
        try:
            ffmpeg = find_ffmpeg(self.project_root)
            separator = self.separator_factory()

            def on_progress(phase: str, percent: int) -> None:
                if record["cancel_event"].is_set():
                    raise RuntimeError("任务已取消")
                record["phase"] = phase
                record["progress"] = percent

            result = run_clone_pipeline(
                task_dir,
                record["video_path"],
                ffmpeg=ffmpeg,
                separator=separator,
                vad_factory=self.vad_factory,
                on_progress=on_progress,
            )
            if record["cancel_event"].is_set():
                raise RuntimeError("任务已取消")
            reference = Path(result["reference_path"])
            apply_result(reference)
            record.update(
                {
                    "state": "succeeded",
                    "phase": "done",
                    "progress": 100,
                    "duration_seconds": result["duration_seconds"],
                    "segment_count": result["segment_count"],
                }
            )
        except RuntimeError as exc:
            if record["cancel_event"].is_set():
                record.update({"state": "cancelled", "phase": "cancelled", "error": ""})
            else:
                record.update({"state": "failed", "error": str(exc)})
        except Exception as exc:  # noqa: BLE001 - 后台任务需要兜底
            record.update({"state": "failed", "error": str(exc)})
