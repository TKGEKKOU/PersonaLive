from pathlib import Path
import time
import zipfile

import pytest

from voice.tts.install import TTSResourceManager


def test_tts_status_requires_lunar_runtime_and_both_models(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)
    manager.runtime_dir.mkdir(parents=True)
    manager.runtime_path.write_bytes(b"exe")
    manager.model_dir.mkdir(parents=True)
    manager.model_path.write_bytes(b"model")

    assert manager.status()["installed"] is False

    manager.runtime_dll_path.write_bytes(b"dll")
    manager.tokenizer_path.write_bytes(b"tokenizer")
    status = manager.status()
    assert status["installed"] is True
    assert status["ready"] is True
    assert status["download_size"] == "约 3 GB"


def test_tts_config_can_disable_ready_resources(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)
    manager.runtime_dir.mkdir(parents=True)
    manager.runtime_path.write_bytes(b"exe")
    manager.runtime_dll_path.write_bytes(b"dll")
    manager.model_dir.mkdir(parents=True)
    manager.model_path.write_bytes(b"model")
    manager.tokenizer_path.write_bytes(b"tokenizer")

    status = manager.configure(enabled=False)

    assert status["installed"] is True
    assert status["ready"] is False


def test_tts_config_can_choose_gpu_acceleration(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)

    status = manager.configure(use_gpu=False)

    assert status["use_gpu"] is False


def test_runtime_archive_rejects_path_traversal(tmp_path: Path):
    manager = TTSResourceManager(tmp_path / "project")
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../outside.exe", b"bad")

    with pytest.raises(RuntimeError, match="不安全"):
        manager._extract_runtime(archive)

    assert not (tmp_path / "outside.exe").exists()


def test_tts_status_exposes_install_progress(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)
    manager._set_progress("model", "voice.gguf", 25, 100)

    status = manager.status()

    assert status["phase"] == "model"
    assert status["current_file"] == "voice.gguf"
    assert status["downloaded_bytes"] == 25
    assert status["total_bytes"] == 100
    assert status["progress_percent"] == 25


def test_tts_status_exposes_download_speed_and_eta(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)
    manager._set_progress("model", "voice.gguf", 50, 100)
    manager._download_started_at = time.monotonic() - 5

    status = manager.status()

    assert status["download_speed_bytes"] > 0
    assert status["eta_seconds"] is not None


def test_cancel_install_marks_active_download_for_cancellation(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)
    manager._installing = True

    assert manager.cancel_install() is True
    assert manager.status()["cancelling"] is True


def test_install_downloads_only_models_when_runtime_is_bundled(tmp_path: Path, monkeypatch):
    manager = TTSResourceManager(tmp_path)
    manager.runtime_path.parent.mkdir(parents=True)
    manager.runtime_path.write_bytes(b"exe")
    manager.runtime_dll_path.write_bytes(b"dll")
    downloaded = []

    def fake_download(url, destination, phase="download"):
        downloaded.append((url, destination.name, phase))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"data")

    monkeypatch.setattr(manager, "_download", fake_download)
    manager._install()

    assert [item[1] for item in downloaded] == [manager.model_path.name, manager.tokenizer_path.name]
    assert {item[2] for item in downloaded} == {"model"}


def test_install_never_downloads_missing_runtime(tmp_path: Path, monkeypatch):
    manager = TTSResourceManager(tmp_path)
    downloaded = []
    monkeypatch.setattr(manager, "_download", lambda *args: downloaded.append(args))

    manager._install()

    assert downloaded == []
    assert "内置 Lunar TTS 运行库" in manager.status()["error"]
