import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable


class DesktopStartupError(RuntimeError):
    pass


class DockerManager:
    def __init__(
        self,
        project_root: Path,
        runner: Callable = subprocess.run,
        docker_executable: str | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.runner = runner
        self.docker = shutil.which("docker") if docker_executable is None else docker_executable

    def _run(self, command: list[str], check: bool = False) -> subprocess.CompletedProcess:
        return self.runner(
            command,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            check=check,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    def is_ready(self) -> bool:
        if not self.docker:
            return False
        return self._run([self.docker, "info"]).returncode == 0

    @staticmethod
    def desktop_candidates() -> list[Path]:
        return [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Docker" / "Docker Desktop.exe",
        ]

    def ensure_ready(self, timeout: int = 120) -> None:
        if not self.docker:
            raise DesktopStartupError("未检测到 Docker Desktop，请先安装并启动 Docker Desktop。")
        if self.is_ready():
            return
        executable = next((path for path in self.desktop_candidates() if path.is_file()), None)
        if executable:
            subprocess.Popen([str(executable)], creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_ready():
                return
            time.sleep(2)
        raise DesktopStartupError("Docker Engine 启动超时，请打开 Docker Desktop 检查状态。")

    def compose_up(self) -> None:
        compose = self.project_root / "docker-compose.yml"
        if not compose.is_file():
            raise DesktopStartupError("缺少 docker-compose.yml。")
        try:
            self._run([self.docker, "compose", "-f", str(compose), "up", "-d", "--wait"], check=True)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise DesktopStartupError(f"Docker 服务启动失败：{detail}") from exc

    def compose_stop(self) -> None:
        """暂停容器（不删除），数据卷保留；下次 compose start/up -d 快速恢复。"""
        compose = self.project_root / "docker-compose.yml"
        if not compose.is_file():
            raise DesktopStartupError("缺少 docker-compose.yml。")
        try:
            self._run([self.docker, "compose", "-f", str(compose), "stop"], check=True)
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "").strip()
            raise DesktopStartupError(f"Docker 服务停止失败：{detail}") from exc
