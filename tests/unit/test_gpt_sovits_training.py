from pathlib import Path

import pytest

from voice.gpt_sovits.config import GPTSoVITSConfig
from voice.gpt_sovits.training import TrainingService


INSTALL = Path(r"D:\CodePython\YUMENO\runtime\gpt_sovits")


@pytest.mark.skipif(
    not (INSTALL / "api_v2.py").is_file(),
    reason="project GPT-SoVITS install not present",
)
def test_build_training_commands_generates_configs(tmp_path: Path):
    config = GPTSoVITSConfig(tmp_path)
    config.save(install_dir=str(INSTALL))
    service = TrainingService(tmp_path, config)
    asset_id = "asset-1"
    service.dataset_dir(asset_id).mkdir(parents=True)

    commands = service.build_training_commands(asset_id)

    assert len(commands) == 6
    stages = [item[0] for item in commands]
    assert stages[0] == "文本预处理"
    assert stages[-2] == "训练 GPT 模型"
    assert stages[-1] == "训练 SoVITS 模型"
    assert all(commands[i][1][0].endswith("python.exe") for i in range(6))

    s1_yaml = service.output_dir(asset_id) / "s1.yaml"
    s2_json = service.output_dir(asset_id) / "s2.json"
    assert s1_yaml.is_file()
    assert s2_json.is_file()
    s1_text = s1_yaml.read_text(encoding="utf-8")
    assert "train_semantic_path" in s1_text
    assert "6-name2semantic.tsv" in s1_text
    s2_data = __import__("json").loads(s2_json.read_text(encoding="utf-8"))
    assert s2_data["model"]["version"] == "v2Pro"
    assert s2_data["data"]["exp_dir"].endswith("exp")


def test_prepare_dataset_writes_list_and_normalizes(tmp_path: Path, monkeypatch):
    config = GPTSoVITSConfig(tmp_path)
    service = TrainingService(tmp_path, config)
    asset_id = "asset-2"
    dataset = service.dataset_dir(asset_id)
    dataset.mkdir(parents=True)
    source = dataset / "a.mp3"
    source.write_bytes(b"mp3")

    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        target = Path(args[-1])
        target.write_bytes(b"wav")
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr("voice.gpt_sovits.training.subprocess.run", fake_run)
    stats = service.prepare_dataset(
        asset_id,
        [str(source)],
        texts=["你好"],
        language="ZH",
    )

    assert stats["count"] == 1
    list_text = (dataset / f"{asset_id}.list").read_text(encoding="utf-8")
    assert list_text.startswith(f"{(dataset / '001.wav').as_posix()}|asset|ZH|你好")
