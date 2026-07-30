from pathlib import Path
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
