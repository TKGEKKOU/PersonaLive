import subprocess
from pathlib import Path
from typing import Callable


class TTSGenerationError(RuntimeError):
    pass


class LocalTTS:
    def __init__(self, cli_path: Path, model_dir: Path, runner: Callable = subprocess.run) -> None:
        self.cli_path = Path(cli_path)
        self.model_dir = Path(model_dir)
        self.runner = runner

    def synthesize(self, text: str, output: Path, reference_audio: Path | None = None) -> Path:
        if not text.strip():
            raise TTSGenerationError("合成文本为空")
        output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        command = [str(self.cli_path), "-m", str(self.model_dir), "-t", text.strip(), "-o", str(output)]
        if reference_audio:
            command.extend(["-r", str(reference_audio)])
        try:
            self.runner(command, check=True, capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", "") or str(exc)
            raise TTSGenerationError(f"本地语音生成失败：{detail[-1000:]}") from exc
        if not output.is_file() or output.stat().st_size == 0:
            raise TTSGenerationError("C++ 推理程序没有生成音频")
        return output
