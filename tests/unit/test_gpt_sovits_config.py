from pathlib import Path

from voice.gpt_sovits.config import (
    GPTSoVITSConfig,
    detect_install_dir,
    probe_installation,
)


def make_install(root: Path) -> Path:
    install = root / "GPT-SoVITS-test"
    runtime = install / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"py")
    (install / "api_v2.py").write_text("APP = None\n", encoding="utf-8")
    return install


def test_probe_detects_v2_install(tmp_path: Path):
    install = make_install(tmp_path)
    probe = probe_installation(install)

    assert probe.ok is True
    assert probe.api_version == "v2"
    assert probe.api_script.name == "api_v2.py"


def test_probe_reports_missing_python(tmp_path: Path):
    install = tmp_path / "bad"
    install.mkdir()
    (install / "api_v2.py").write_text("", encoding="utf-8")

    probe = probe_installation(install)

    assert probe.ok is False
    assert "Python" in probe.error


def test_detect_install_dir_scans_drive(tmp_path: Path):
    install = make_install(tmp_path)
    found = detect_install_dir(roots=(tmp_path,))

    assert found == install.resolve()


def test_config_persists_install_dir(tmp_path: Path):
    config = GPTSoVITSConfig(tmp_path)

    values = config.save(install_dir="D:/GPT-SoVITS-x", api_port=9999)

    assert values["install_dir"] == "D:/GPT-SoVITS-x"
    assert values["api_port"] == 9999
    assert GPTSoVITSConfig(tmp_path).values() == values
