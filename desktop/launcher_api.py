import threading
import time
import socket
import tomllib
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

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
        self._start_thread: threading.Thread | None = None
        self._start_done = False
        self._start_result: dict | None = None
        self._steps: dict[str, dict] = {
            "docker": {"label": "Docker", "state": "pending", "detail": "等待中"},
            "containers": {"label": "容器", "state": "pending", "detail": "等待中"},
            "mysql": {"label": "MySQL", "state": "pending", "detail": "等待中"},
            "milvus": {"label": "Milvus", "state": "pending", "detail": "等待中"},
            "service": {"label": "本地服务", "state": "pending", "detail": "等待中"},
        }

    def bind_window(self, window) -> None:
        self._window = window

    def onboarding_url(self) -> str:
        return str(self.project_root / "resources" / "onboarding.html")

    def status(self) -> dict:
        milvus_port = self._milvus_port()
        return {
            "docker_ready": self.docker.is_ready(),
            "containers_up": self._containers_up(),
            "mysql_up": self._port_open(self.settings.mysql_port),
            "milvus_up": self._port_open(milvus_port),
            "service_running": self.server.is_running(),
            "url": self.server.url,
            "port": self.settings.app_port,
            "mysql_port": self.settings.mysql_port,
            "milvus_port": milvus_port,
            "attu_port": 17003,
            "version": self._app_version(),
        }

    @staticmethod
    def _app_version() -> str:
        try:
            with open(Path(__file__).resolve().parents[1] / "pyproject.toml", "rb") as handle:
                return str(tomllib.load(handle)["project"]["version"])
        except Exception:
            return "dev"

    @staticmethod
    def _port_open(port: int) -> bool:
        try:
            with socket.socket() as sock:
                sock.settimeout(0.5)
                return sock.connect_ex(("127.0.0.1", port)) == 0
        except Exception:
            return False

    def _containers_up(self) -> bool:
        try:
            if not self.docker.is_ready():
                return False
            result = self.docker._run([self.docker.docker, "compose", "ps", "-q"])
            return result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            return False

    def open_external(self, url: str) -> None:
        """在系统默认浏览器中打开外部链接（pywebview 内 target=_blank 不可靠）。"""
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def start(self) -> dict:
        if self._start_thread is not None and self._start_thread.is_alive():
            return {"ok": True, "starting": True}
        for step in self._steps.values():
            step["state"] = "pending"
            step["detail"] = "等待中"
        self._start_done = False
        self._start_result = None
        self._start_thread = threading.Thread(
            target=self._start_worker, daemon=True, name="personalive-start"
        )
        self._start_thread.start()
        return {"ok": True, "starting": True}

    def progress(self) -> dict:
        return {
            "starting": self._start_thread is not None and self._start_thread.is_alive(),
            "done": self._start_done,
            "ok": (self._start_result or {}).get("ok"),
            "error": (self._start_result or {}).get("error", ""),
            "steps": [
                {
                    "key": key,
                    "label": item["label"],
                    "state": item["state"],
                    "detail": item["detail"],
                }
                for key, item in self._steps.items()
            ],
        }

    def _set_step(self, key: str, state: str, detail: str) -> None:
        step = self._steps.get(key)
        if step is not None:
            step["state"] = state
            step["detail"] = detail

    def _fail_running_steps(self, message: str) -> None:
        for step in self._steps.values():
            if step["state"] == "running":
                step["state"] = "fail"
                step["detail"] = message

    def _milvus_port(self) -> int:
        try:
            return urlsplit(self.settings.milvus_uri).port or 19530
        except Exception:
            return 19530

    def _wait_port(self, port: int, timeout: int = 120) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._port_open(port):
                return True
            time.sleep(1)
        return False

    def _wait_http(self, timeout: int = 15) -> bool:
        import httpx

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{self.server.url}/api/health", timeout=1, trust_env=False).is_success:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _start_worker(self) -> None:
        try:
            self._set_step("docker", "running", "等待 Docker Desktop 就绪…")
            self.docker.ensure_ready()
            self._set_step("docker", "ok", "Docker Engine 就绪")
            self._set_step("containers", "running", "正在启动 MySQL / Milvus 容器…")
            self.docker.compose_up()
            self._set_step("containers", "ok", "容器已启动")
            self._set_step("mysql", "running", f"等待 MySQL（127.0.0.1:{self.settings.mysql_port}）…")
            if not self._wait_port(self.settings.mysql_port):
                raise RuntimeError("MySQL 启动超时，请检查 Docker 容器状态")
            self._set_step("mysql", "ok", "MySQL 已连接")
            milvus_port = self._milvus_port()
            self._set_step("milvus", "running", f"等待 Milvus（127.0.0.1:{milvus_port}）…")
            if not self._wait_port(milvus_port):
                raise RuntimeError("Milvus 启动超时，请检查 Docker 容器状态")
            self._set_step("milvus", "ok", "Milvus 已连接")
            if self.server.is_running():
                self._set_step("service", "running", "检测到本地服务，正在校验…")
            else:
                self._set_step("service", "running", "正在启动本地服务…")
                self.server.start()
            if not self._wait_http():
                raise RuntimeError("本地服务健康检查未通过，请稍后重试")
            self._register_shutdown_callback()
            self._set_step("service", "ok", "服务已就绪")
            self._start_result = {"ok": True, "url": self.server.url}
        except Exception as exc:
            self._fail_running_steps(str(exc))
            self._start_result = {"ok": False, "error": str(exc)}
        finally:
            self._start_done = True

    def show_main(self) -> None:
        if self._window is not None:
            self._window.load_url(f"{self.server.url}/static/index.html")

    def show_launcher(self) -> None:
        if self._window is not None:
            self._window.load_url(self.onboarding_url())

    def show_docker_settings(self) -> None:
        if self._window is not None:
            self._window.load_url(f"{self.server.url}/static/index.html#docker-exit")

    def request_exit_confirm(self) -> None:
        if self._window is not None:
            try:
                self._window.evaluate_js("window.showExitConfirm && window.showExitConfirm()")
            except Exception:
                pass

    def on_closing(self) -> bool:
        """pywebview closing 回调：阻止直接关闭，改由确认框决定。"""
        if self._exiting:
            return True
        threading.Thread(target=self._delayed_exit_confirm, daemon=True).start()
        return False

    def _delayed_exit_confirm(self) -> None:
        time.sleep(0.1)
        self.request_exit_confirm()

    def do_exit(self) -> None:
        self._exiting = True
        self._apply_exit_policy()
        self.server.stop()
        from voice.asr.local_worker import shutdown_asr_workers

        shutdown_asr_workers()
        if self._window is not None:
            self._window.destroy()

    def get_exit_policy(self) -> dict:
        try:
            from extensions.storage import read_json

            values = read_json(self.project_root / "data" / "docker_settings.json")
            return {"on_exit": values.get("on_exit", "pause")}
        except Exception:
            return {"on_exit": "pause"}

    def set_exit_policy(self, policy: str) -> dict:
        """保存退出时的 Docker 处理方式：keep（保持运行）/ pause（停止）/ remove（完全清除）。"""
        if policy not in {"keep", "pause", "remove"}:
            return {"ok": False, "error": "无效的处理方式"}
        try:
            from extensions.storage import write_json

            write_json(
                self.project_root / "data" / "docker_settings.json",
                {"on_exit": policy},
            )
            return {"ok": True, "on_exit": policy}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

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
