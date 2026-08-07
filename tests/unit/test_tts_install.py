from pathlib import Path
import time

from voice.tts.install import TTSResourceManager


def make_lunar(manager: TTSResourceManager) -> None:
    manager.runtime_dir.mkdir(parents=True, exist_ok=True)
    manager.runtime_path.write_bytes(b"exe")
    manager.runtime_dll_path.write_bytes(b"dll")
    manager.model_dir.mkdir(parents=True, exist_ok=True)
    manager.model_path.write_bytes(b"model")
    manager.tokenizer_path.write_bytes(b"tokenizer")


def test_tts_status_requires_runtime_and_both_models(tmp_path: Path):
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
    assert status["download_size"] == "约 2.2 GB，质量优先（默认），运行库已内置"
    assert status["runtime_bundled"] is True


def test_tts_config_can_disable_ready_resources(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)
    make_lunar(manager)

    status = manager.configure(enabled=False)

    assert status["installed"] is True
    assert status["ready"] is False


def test_tts_config_can_choose_gpu_acceleration(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)

    status = manager.configure(use_gpu=False)

    assert status["use_gpu"] is False


def test_tts_status_exposes_install_progress(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)
    manager.state.set_progress("model", "voice.gguf", 25, 100)
    manager.state.source = "modelscope.cn"

    status = manager.status()

    assert status["phase"] == "model"
    assert status["current_file"] == "voice.gguf"
    assert status["downloaded_bytes"] == 25
    assert status["total_bytes"] == 100
    assert status["progress_percent"] == 25
    assert status["source"] == "modelscope.cn"


def test_tts_status_exposes_download_speed_and_eta(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)
    manager.state.set_progress("model", "voice.gguf", 50, 100)
    manager.state.started_at = time.monotonic() - 5

    status = manager.status()

    assert status["download_speed_bytes"] > 0
    assert status["eta_seconds"] is not None
    assert status["elapsed_seconds"] >= 4


def test_cancel_install_marks_active_download_for_cancellation(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)
    manager.state.installing = True

    assert manager.cancel_install() is True
    assert manager.status()["cancelling"] is True


def test_install_downloads_only_models_when_runtime_is_bundled(tmp_path: Path, monkeypatch):
    manager = TTSResourceManager(tmp_path)
    make_lunar(manager)
    manager.model_path.unlink()
    manager.tokenizer_path.unlink()
    downloaded = []

    def fake_download(state, url, destination, phase="model"):
        downloaded.append((url, destination.name, phase))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"data")

    monkeypatch.setattr(manager, "_download", fake_download)
    manager._install()

    assert [item[1] for item in downloaded] == [manager.model_path.name, manager.tokenizer_path.name]
    assert {item[2] for item in downloaded} == {"model"}
    assert manager.state.phase == "complete"


def test_install_never_downloads_missing_runtime(tmp_path: Path, monkeypatch):
    manager = TTSResourceManager(tmp_path)
    downloaded = []
    monkeypatch.setattr(manager, "_download", lambda *args: downloaded.append(args))

    manager._install()

    assert downloaded == []
    assert "缺少内置 Lunar TTS 运行库" in manager.state.error


def test_install_skips_existing_model_files(tmp_path: Path, monkeypatch):
    manager = TTSResourceManager(tmp_path)
    make_lunar(manager)
    downloaded = []
    monkeypatch.setattr(manager, "_download", lambda *args: downloaded.append(args))

    manager._install()

    assert downloaded == []


def test_remove_models_removes_model_dir(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)
    make_lunar(manager)

    status = manager.remove_models()

    assert not manager.model_dir.exists()
    assert status["installed"] is False
    assert manager.runtime_path.is_file() is True


def test_tts_config_tracks_engine(tmp_path: Path):
    manager = TTSResourceManager(tmp_path)

    assert manager.config()["engine"] == "lunar"

    status = manager.configure(engine="gpt_sovits")

    assert status["engine"] == "gpt_sovits"
    assert manager.config()["engine"] == "gpt_sovits"
