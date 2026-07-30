from pathlib import Path

import voice.asr.install as asr_install

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
    manager.runtime_dir.mkdir(parents=True)
    manager.managed_model.mkdir(parents=True)

    manager.remove_managed()

    assert external_model.is_dir()
    assert not manager.runtime_dir.exists()
    assert not manager.managed_model.exists()


def test_project_release_resources_are_resolved(tmp_path):
    manager = ASRResourceManager(tmp_path)
    python = make_file(manager.runtime_python)
    model = manager.managed_model
    model.mkdir(parents=True)
    make_file(model / "config.json")
    ffmpeg = make_file(manager.managed_ffmpeg)

    resources = manager.resolve()

    assert resources.python == python.resolve()
    assert resources.model == model.resolve()
    assert resources.ffmpeg == ffmpeg.resolve()


def test_install_pip_command_has_bounded_network_retries(tmp_path, monkeypatch):
    manager = ASRResourceManager(tmp_path)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[:3] == [str(manager.runtime_python), "-m", "pip"]:
            raise asr_install.subprocess.CalledProcessError(1, command, stderr="unreachable")

    monkeypatch.setattr(asr_install.subprocess, "run", fake_run)
    manager._install()

    pip_command = next(command for command in commands if command[:3] == [str(manager.runtime_python), "-m", "pip"])
    assert pip_command[pip_command.index("--timeout") + 1] == "30"
    assert pip_command[pip_command.index("--retries") + 1] == "1"
