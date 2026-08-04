import ctypes
import os
from pathlib import Path
import shutil
import sys

from app.main import create_app
from desktop.docker_manager import DesktopStartupError, DockerManager
from desktop.launcher_api import LauncherApi
from desktop.server_manager import ServerManager
from voice.asr.local_worker import shutdown_asr_workers


def show_error(message: str) -> None:
    ctypes.windll.user32.MessageBoxW(0, message, "PersonaLive 启动失败", 0x10)


def apply_window_icon(window, icon_path: Path) -> None:
    """给 pywebview 窗口设置任务栏/标题栏图标（开发模式下 exe 图标不生效）。

    pywebview 5.x 的 edgechromium 窗口是 WinForms 容器，window.native.form.Handle
    即窗口句柄；通过 WM_SETICON 发送大/小两个图标。
    """

    try:
        native = window.native
        form = native.form
        hwnd = int(form.Handle)
        if not hwnd:
            return
        WM_SETICON = 0x0080
        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        for size, icon_index in ((32, 1), (16, 0)):  # ICON_BIG / ICON_SMALL
            hicon = ctypes.windll.user32.LoadImageW(
                None,
                str(icon_path),
                IMAGE_ICON,
                size,
                size,
                LR_LOADFROMFILE,
            )
            if hicon:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, icon_index, hicon)
    except Exception:
        # 图标设置失败不影响应用启动。
        pass


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
    api = LauncherApi(root, docker, server)
    try:
        ensure_local_env(root)
        import webview

        if server.is_running():
            initial_url = f"{server.url}/static/index.html"
        else:
            initial_url = api.onboarding_url()
        window = webview.create_window(
            "PersonaLive",
            initial_url,
            width=1280,
            height=820,
            min_size=(960, 640),
            js_api=api,
        )
        api.bind_window(window)
        api.auto_start_if_needed()
        window.events.closing += api.on_closing
        window.events.closed += lambda: (server.stop(), shutdown_asr_workers())
        os.environ.setdefault(
            "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
            "--autoplay-policy=no-user-gesture-required",
        )
        webview.start(
            gui="edgechromium",
            debug=False,
            private_mode=False,
            func=lambda: apply_window_icon(window, root / "resources" / "app.ico"),
        )
        return 0
    except (DesktopStartupError, RuntimeError, OSError, ImportError) as exc:
        server.stop()
        shutdown_asr_workers()
        show_error(str(exc))
        return 1
