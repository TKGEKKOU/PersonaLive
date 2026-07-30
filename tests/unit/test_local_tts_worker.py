from pathlib import Path

import pytest

from voice.tts.local_worker import LocalTTS, TTSGenerationError


def test_local_tts_invokes_cpp_cli(tmp_path: Path):
    cli = tmp_path / "qwen3-tts-cli.exe"
    model_dir = tmp_path / "models"
    reference = tmp_path / "voice.wav"
    output = tmp_path / "reply.wav"
    cli.write_bytes(b"exe")
    model_dir.mkdir()
    reference.write_bytes(b"wav")
    captured = {}

    def runner(command, **kwargs):
        captured["command"] = command
        Path(command[command.index("-o") + 1]).write_bytes(b"RIFFaudio")

    LocalTTS(cli, model_dir, runner=runner).synthesize("你好", output, reference)

    assert captured["command"] == [str(cli), "-m", str(model_dir), "-t", "你好", "-o", str(output), "-r", str(reference)]
    assert output.read_bytes() == b"RIFFaudio"


def test_local_tts_rejects_missing_output(tmp_path: Path):
    cli = tmp_path / "qwen3-tts-cli.exe"
    cli.write_bytes(b"exe")

    with pytest.raises(TTSGenerationError, match="没有生成音频"):
        LocalTTS(cli, tmp_path, runner=lambda *args, **kwargs: None).synthesize("hello", tmp_path / "out.wav")
