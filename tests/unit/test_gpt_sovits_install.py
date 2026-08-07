import shutil
import zipfile
from pathlib import Path

from voice.gpt_sovits.config import GPTSoVITSConfig
from voice.gpt_sovits.install import GPTSoVITSInstallManager


def build_package(tmp_path: Path) -> Path:
    archive = tmp_path / "downloads" / "pkg.zip"
    archive.parent.mkdir(parents=True)
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("GPT-SoVITS/runtime/python.exe", b"py")
        bundle.writestr("GPT-SoVITS/api_v2.py", "APP = None\n")
    return archive


def test_installer_downloads_extracts_and_registers(tmp_path: Path, monkeypatch):
    config = GPTSoVITSConfig(tmp_path)
    manager = GPTSoVITSInstallManager(tmp_path, config)
    archive = build_package(tmp_path)

    def fake_download(url, destination):
        shutil.copy2(archive, destination)

    monkeypatch.setattr(manager, "_download", fake_download)
    manager._install("http://example.invalid/pkg.zip")

    status = manager.status()
    assert status["installed"] is True
    assert status["phase"] == "complete"
    assert config.values()["install_dir"] == str(manager.install_dir)
    assert (manager.install_dir / "runtime" / "python.exe").is_file()


def test_installer_applies_project_patches(tmp_path: Path, monkeypatch):
    config = GPTSoVITSConfig(tmp_path)
    manager = GPTSoVITSInstallManager(tmp_path, config)
    archive = build_package(tmp_path)
    patch = manager.patches_dir / "api_v2.py"
    patch.parent.mkdir(parents=True)
    patch.write_text("PATCHED = True\n", encoding="utf-8")

    def fake_download(url, destination):
        shutil.copy2(archive, destination)

    monkeypatch.setattr(manager, "_download", fake_download)
    manager._install("http://example.invalid/pkg.zip")

    assert (manager.install_dir / "api_v2.py").read_text(encoding="utf-8") == "PATCHED = True\n"


def test_installer_slims_unused_content_and_removes_archive(tmp_path: Path, monkeypatch):
    config = GPTSoVITSConfig(tmp_path)
    manager = GPTSoVITSInstallManager(tmp_path, config)
    archive = build_package(tmp_path)

    # 模拟官方包携带的冗余内容（解压后存在），瘦身步骤必须删掉。
    def fake_apply_patches():
        unused_asr = manager.install_dir / "tools" / "asr" / "models"
        unused_asr.mkdir(parents=True)
        (unused_asr / "model.pt").write_bytes(b"asr")
        unused_v4 = manager.install_dir / "GPT_SoVITS" / "pretrained_models" / "gsv-v4-pretrained"
        unused_v4.mkdir(parents=True)
        (unused_v4 / "s2Gv4.pth").write_bytes(b"v4")
        (manager.install_dir / "GPT_SoVITS" / "pretrained_models" / "s2Gv3.pth").write_bytes(b"v3")

    monkeypatch.setattr(manager, "_apply_patches", fake_apply_patches)

    def fake_download(url, destination):
        shutil.copy2(archive, destination)

    monkeypatch.setattr(manager, "_download", fake_download)
    manager._install("http://example.invalid/pkg.zip")

    status = manager.status()
    assert status["phase"] == "complete"
    assert not (manager.install_dir / "tools" / "asr").exists()
    assert not (manager.install_dir / "GPT_SoVITS" / "pretrained_models" / "gsv-v4-pretrained").exists()
    assert not (manager.install_dir / "GPT_SoVITS" / "pretrained_models" / "s2Gv3.pth").exists()
    # 成功后下载缓存被清理；保留路径仍在
    assert not (manager.download_dir / "pkg.zip").exists()
    assert (manager.install_dir / "runtime" / "python.exe").is_file()
    assert (manager.install_dir / "api_v2.py").is_file()


def test_installer_reports_missing_url(tmp_path: Path):
    manager = GPTSoVITSInstallManager(tmp_path, GPTSoVITSConfig(tmp_path))
    try:
        manager.start_install("")
    except ValueError as exc:
        assert "下载地址" in str(exc)
    else:
        raise AssertionError("expected ValueError for empty url")


def test_installer_extracts_7z_with_seven_zip(tmp_path: Path, monkeypatch):
    from voice.gpt_sovits.config import GPTSoVITSConfig
    from voice.gpt_sovits.install import GPTSoVITSInstallManager

    config = GPTSoVITSConfig(tmp_path)
    manager = GPTSoVITSInstallManager(tmp_path, config)
    archive = tmp_path / "pkg.7z"
    archive.write_bytes(b"7z")

    def fake_which(name):
        return "C:/7-Zip/7z.exe"

    class FakePopen:
        def __init__(self, args, **kwargs):
            target = tmp_path / "runtime" / "gpt_sovits.tmp"
            (target / "GPT-SoVITS" / "runtime").mkdir(parents=True)
            (target / "GPT-SoVITS" / "api_v2.py").write_text("APP=None\n", encoding="utf-8")
            self.stdout = iter(["  0%    \n", "- GPT-SoVITS/api_v2.py\n", "100% 1\n"])

        def kill(self):
            pass

        def wait(self):
            return 0

    def fake_popen(args, **kwargs):
        return FakePopen(args, **kwargs)

    monkeypatch.setattr("voice.gpt_sovits.install.shutil.which", fake_which)
    monkeypatch.setattr("voice.gpt_sovits.install.subprocess.Popen", fake_popen)

    manager._extract(archive)

    assert (manager.install_dir / "api_v2.py").is_file()
    assert (manager.install_dir / "runtime").is_dir()


def test_installer_reports_extract_progress_phases(tmp_path: Path, monkeypatch):
    """Extract and patch phases report granular progress to the state."""

    from voice.gpt_sovits.config import GPTSoVITSConfig
    from voice.gpt_sovits.install import GPTSoVITSInstallManager

    config = GPTSoVITSConfig(tmp_path)
    manager = GPTSoVITSInstallManager(tmp_path, config)
    archive = build_package(tmp_path)

    def fake_download(url, destination):
        shutil.copy2(archive, destination)

    monkeypatch.setattr(manager, "_download", fake_download)
    manager._install("http://example.invalid/pkg.zip")

    status = manager.status()
    assert status["phase"] == "complete"
    assert status["progress_percent"] in (None, 100)
    assert (manager.install_dir / "api_v2.py").is_file()


def test_installer_7z_phase_name(tmp_path: Path, monkeypatch):
    """Old ``extract`` phase is replaced by granular ``extracting``."""

    from voice.gpt_sovits.config import GPTSoVITSConfig
    from voice.gpt_sovits.install import GPTSoVITSInstallManager

    config = GPTSoVITSConfig(tmp_path)
    manager = GPTSoVITSInstallManager(tmp_path, config)
    archive = tmp_path / "pkg.7z"
    archive.write_bytes(b"7z")

    class FakePopen:
        def __init__(self, args, **kwargs):
            target = tmp_path / "runtime" / "gpt_sovits.tmp"
            (target / "GPT-SoVITS").mkdir(parents=True)
            (target / "GPT-SoVITS" / "api_v2.py").write_text("APP=None\n", encoding="utf-8")
            self.stdout = iter(["  0%    \n", "100% 1\n"])

        def kill(self):
            pass

        def wait(self):
            return 0

    monkeypatch.setattr(
        "voice.gpt_sovits.install.shutil.which",
        lambda name: "C:/7-Zip/7z.exe",
    )
    monkeypatch.setattr(
        "voice.gpt_sovits.install.subprocess.Popen",
        lambda args, **kwargs: FakePopen(args, **kwargs),
    )

    manager._extract(archive)
    assert (manager.install_dir / "api_v2.py").is_file()
    assert manager.state.phase == "extracting"


def test_installer_uses_new_phase_names(tmp_path: Path):
    """The installer state exposes the granular phase vocabulary."""

    from voice.gpt_sovits.config import GPTSoVITSConfig
    from voice.gpt_sovits.install import GPTSoVITSInstallManager

    manager = GPTSoVITSInstallManager(tmp_path, GPTSoVITSConfig(tmp_path))
    manager.state.set_progress("extracting", "pkg.7z", 50, 100, detail="解压 50%")
    snapshot = manager.status()
    assert snapshot["phase"] == "extracting"
    assert snapshot["detail"] == "解压 50%"
    assert snapshot["progress_percent"] == 50
    assert snapshot["download_speed_bytes"] == 0


def test_installer_7z_popen_failure(tmp_path: Path, monkeypatch):
    from voice.gpt_sovits.config import GPTSoVITSConfig
    from voice.gpt_sovits.install import GPTSoVITSInstallManager

    config = GPTSoVITSConfig(tmp_path)
    manager = GPTSoVITSInstallManager(tmp_path, config)
    archive = tmp_path / "pkg.7z"
    archive.write_bytes(b"7z")

    class FakePopen:
        def __init__(self, args, **kwargs):
            self.stdout = iter([])

        def kill(self):
            pass

        def wait(self):
            return 2

    monkeypatch.setattr(
        "voice.gpt_sovits.install.shutil.which",
        lambda name: "C:/7-Zip/7z.exe",
    )
    monkeypatch.setattr(
        "voice.gpt_sovits.install.subprocess.Popen",
        lambda args, **kwargs: FakePopen(args, **kwargs),
    )

    try:
        manager._extract(archive)
    except RuntimeError as exc:
        assert "退出码" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for failing 7-Zip")
