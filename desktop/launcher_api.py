from pathlib import Path

from desktop.docker_manager import DockerManager
from desktop.server_manager import ServerManager
from settings import Settings


class LauncherApi:
    """PyWebView js_api：启动页调用的 Python 方法。"""

    def __init__(self, project_root: Path, docker: DockerManager, server: ServerManager) -> None:
        self.project_root = project_root
        self.docker = docker
        self.server = server
        self.settings = server.settings
        self._window = None
        self._exiting = False

    def bind_window(self, window) -> None:
        self._window = window

    def onboarding_url(self) -> str:
        return str(self.project_root / "resources" / "onboarding.html")

    def status(self) -> dict:
        return {
            "docker_ready": self.docker.is_ready(),
            "containers_up": self._containers_up(),
            "service_running": self.server.is_running(),
            "url": self.server.url,
            "port": self.settings.app_port,
        }

    def _containers_up(self) -> bool:
        if not self.docker.is_ready():
            return False
        result = self.docker._run([self.docker.docker, "compose", "ps", "-q"])
        return result.returncode == 0 and bool(result.stdout.strip())

    def start(self) -> dict:
        try:
            if not self.server.is_running():
                self.docker.ensure_ready()
                self.docker.compose_up()
                self.server.start()
                self._register_shutdown_callback()
            return {"ok": True, "url": self.server.url}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def show_main(self) -> None:
        if self._window is not None:
            self._window.load_url(f"{self.server.url}/static/index.html")

    def show_launcher(self) -> None:
        if self._window is not None:
            self._window.load_url(self.onboarding_url())

    def request_exit_confirm(self) -> None:
        if self._window is not None:
            self._window.evaluate_js("window.showExitConfirm && window.showExitConfirm()")

    def do_exit(self) -> None:
        self._exiting = True
        self._apply_exit_policy()
        self.server.stop()
        from voice.asr.local_worker import shutdown_asr_workers

        shutdown_asr_workers()
        if self._window is not None:
            self._window.destroy()

    def _apply_exit_policy(self) -> None:
        policy = "keep"
        try:
            from extensions.storage import read_json

            values = read_json(self.project_root / "data" / "docker_settings.json")
            policy = values.get("on_exit", "keep")
        except Exception:
            pass
        if policy == "pause":
            try:
                self.docker.compose_stop()
            except Exception:
                pass
        elif policy == "remove":
            try:
                self.docker.compose_down()
            except Exception:
                pass

    def _register_shutdown_callback(self) -> None:
        app = self.server.app
        if app is None:
            return

        def desktop_shutdown(stop_docker: bool = False) -> None:
            self.server.stop()
            from voice.asr.local_worker import shutdown_asr_workers

            shutdown_asr_workers()
            if stop_docker:
                try:
                    self.docker.compose_stop()
                except Exception:
                    pass
            self.show_launcher()

        app.state.shutdown_callback = desktop_shutdown
