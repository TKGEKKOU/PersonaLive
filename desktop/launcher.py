import ctypes
from pathlib import Path
import shutil
import sys

from app.main import create_app
from desktop.docker_manager import DesktopStartupError, DockerManager
from desktop.server_manager import ServerManager
from voice.asr.local_worker import shutdown_asr_workers


def show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, message, "PersonaLive 启动失败", 0x10)


def ensure_local_env(project_root: Path) -> None:
    target = project_root / ".env"
    example = project_root / ".env.example"
    if not target.exists() and example.is_file():
        shutil.copy2(example, target)


def run(project_root: Path | None = None) -> int:
    default_root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    root = (project_root or default_root).resolve()
    docker = DockerManager(root)
    server = ServerManager(create_app)
    try:
        if not server.is_running():
            ensure_local_env(root)
            docker.ensure_ready()
            docker.compose_up()
        server.start()
        import webview

        window = webview.create_window("PersonaLive", f"{server.url}/static/index.html", width=1280, height=820, min_size=(960, 640))
        window.events.closed += lambda: (server.stop(), shutdown_asr_workers())
        webview.start(gui="edgechromium", debug=False, private_mode=False)
        return 0
    except (DesktopStartupError, RuntimeError, OSError, ImportError) as exc:
        server.stop()
        shutdown_asr_workers()
        show_error(str(exc))
        return 1
