from pathlib import Path

from voice.asr.install import ASRResourceManager


def make_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("test", encoding="utf-8")
    return path


def test_configured_resources_are_persisted_and_preferred(tmp_path):
    python = make_file(tmp_path / "external" / "python.exe")
    model = tmp_path / "external" / "model"
    model.mkdir(parents=True)
    make_file(model / "config.json")
    ffmpeg = make_file(tmp_path / "external" / "ffmpeg.exe")
    manager = ASRResourceManager(tmp_path)

    manager.configure(enabled=True, python_path=str(python), model_path=str(model), ffmpeg_path=str(ffmpeg))
    reloaded = ASRResourceManager(tmp_path)

    assert reloaded.status()["ready"] is True
    assert reloaded.resolve().model == model


def test_remove_managed_never_deletes_external_resources(tmp_path):
    external_model = tmp_path.parent / "external-model"
    external_model.mkdir()
    manager = ASRResourceManager(tmp_path)
    manager.configure(enabled=True, model_path=str(external_model))
    manager.runtime_dir.mkdir()
    manager.managed_model.mkdir(parents=True)

    manager.remove_managed()

    assert external_model.is_dir()
    assert not manager.runtime_dir.exists()
    assert not manager.managed_model.exists()
