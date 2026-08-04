import threading
import time
import socket
import tomllib
import shutil
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from desktop.docker_manager import DockerManager
from desktop.launcher_progress_server import LauncherProgressServer
from desktop.server_manager import ServerManager
from settings import Settings


_STEP_ORDER = ("docker", "containers", "mysql", "milvus", "attu", "service")
_STEP_WEIGHTS = {"docker": 0.15, "containers": 0.38, "mysql": 0.56, "milvus": 0.74, "attu": 0.84, "service": 0.94}


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
        self._step_started: dict[str, float] = {}
        self._step_tickers: dict[str, threading.Thread] = {}
        try:
            self._progress_server = LauncherProgressServer(self.progress)
            self._progress_server.start()
        except OSError:
            self._progress_server = None
        self._steps: dict[str, dict] = {
            "docker": {"label": "Docker", "state": "pending", "detail": "准备中"},
            "containers": {"label": "容器", "state": "pending", "detail": "准备中"},
            "mysql": {"label": "MySQL", "state": "pending", "detail": "准备中"},
            "milvus": {"label": "Milvus", "state": "pending", "detail": "准备中"},
            "attu": {"label": "Attu", "state": "pending", "detail": "准备中"},
            "service": {"label": "本地服务", "state": "pending", "detail": "准备中"},
        }

    def bind_window(self, window) -> None:
        self._window = window

    def auto_start_if_needed(self) -> None:
        """桌面启动时的后端兜底：服务未运行时自动开始启动流程。

        不依赖前端页面与 pywebview API 的连接——即使页面因缓存/时序问题
        没能触发 start()，这里也会把依赖服务与应用拉起来；前端轮询到进度
        后照常展示并跳转主界面。
        """
        try:
            if self.server.is_running():
                return
            if self._start_thread is not None and self._start_thread.is_alive():
                return
            self.start()
        except Exception:
            pass

    def onboarding_url(self) -> str:
        """返回启动页 URL（普通文件路径，不带查询串）。

        WebView2 会缓存 file:// 页面，且 file:// 带查询串会解析失败（Windows）。
        因此按源文件修改时间复制成带版本号的副本文件：源文件一变化，URL 就
        变成新文件名，天然绕过缓存，同时不破坏 file:// 加载。
        副本中会注入本地服务地址，供启动页通过 HTTP 轮询启动进度。
        """
        source = self.project_root / "resources" / "onboarding.html"
        try:
            stamp = int(source.stat().st_mtime)
        except OSError:
            stamp = 0
        target = self.project_root / "data" / "cache" / "launcher" / f"onboarding_{stamp}.html"
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                content = source.read_text(encoding="utf-8")
                base = f"http://127.0.0.1:{self.settings.app_port}"
                progress_url = self._progress_server.url if self._progress_server else ""
                content = content.replace("__LAUNCHER_BASE__", base).replace("__LAUNCHER_PROGRESS__", progress_url)
                target.write_text(content, encoding="utf-8")
            except OSError:
                return str(source)
            for stale in target.parent.glob("onboarding_*.html"):
                if stale != target:
                    try:
                        stale.unlink()
                    except OSError:
                        pass
        return str(target)

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
            step["detail"] = "准备中"
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
            "percent": self._progress_percent(),
            "steps": [
                {
                    "key": key,
                    "label": item["label"],
                    "state": item["state"],
                    "detail": item["detail"],
                    "elapsed": (
                        int(time.monotonic() - self._step_started[key])
                        if item["state"] == "running" and key in self._step_started
                        else None
                    ),
                }
                for key, item in self._steps.items()
            ],
        }

    def _progress_percent(self) -> int:
        """根据各步骤状态估算整体进度（0-100）。"""
        if self._start_done and (self._start_result or {}).get("ok"):
            return 100
        base = 0.0
        for key in _STEP_ORDER:
            weight = _STEP_WEIGHTS[key]
            state = self._steps[key]["state"]
            if state == "ok":
                base = weight
            elif state == "running":
                return round((base + (weight - base) * 0.45) * 100)
            elif state == "fail":
                return round(base * 100)
        return round(base * 100)

    def _set_step(self, key: str, state: str, detail: str) -> None:
        step = self._steps.get(key)
        prev = None
        if step is not None:
            prev = step["state"]
            step["state"] = state
            step["detail"] = detail
        if state == "running":
            # 仅当从未 running 进入 running 时重置计时；running 期间的
            # detail 刷新（如容器状态定时更新）不得重置，否则 elapsed 永远归零
            if prev != "running":
                self._step_started[key] = time.monotonic()
        else:
            self._step_started.pop(key, None)

    def _tick_step_detail(self, key: str, detail_fn, interval: float = 2.0) -> None:
        """步骤 running 期间周期性刷新其 detail（如容器实时状态），提供长等待反馈。"""

        def _run() -> None:
            while self._steps.get(key, {}).get("state") == "running":
                try:
                    self._set_step(key, "running", detail_fn())
                except Exception:
                    pass
                time.sleep(interval)

        thread = threading.Thread(target=_run, daemon=True, name=f"step-ticker-{key}")
        self._step_tickers[key] = thread
        thread.start()

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

    def _wait_port(self, port: int, timeout: int = 120, on_tick=None) -> bool:
        deadline = time.monotonic() + timeout
        last_refresh = 0.0
        while time.monotonic() < deadline:
            if self._port_open(port):
                return True
            now = time.monotonic()
            if on_tick is not None and now - last_refresh >= 2.0:
                last_refresh = now
                try:
                    on_tick()
                except Exception:
                    pass
            time.sleep(1)
        return False

    def _compose_summary(self) -> str:
        """读取 docker compose ps，汇总各容器状态（MySQL / etcd / MinIO / Milvus / Attu）。"""
        try:
            result = self.docker._run(
                [self.docker.docker, "compose", "ps", "--format", "{{.Name}}|{{.Status}}"]
            )
            if result.returncode != 0 or not result.stdout.strip():
                return "容器状态读取中…"
            names = {
                "personalive-mysql": "MySQL",
                "personalive-etcd": "etcd",
                "personalive-minio": "MinIO",
                "personalive-milvus": "Milvus",
                "personalive-attu": "Attu",
            }
            parts = []
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if "|" not in line:
                    continue
                container, status = line.split("|", 1)
                friendly = names.get(container.strip(), container.strip())
                lowered = status.lower()
                if "healthy" in lowered:
                    state = "运行中"
                elif lowered.startswith("up") or "created" in lowered or "restart" in lowered:
                    state = "启动中"
                else:
                    state = "未启动"
                parts.append(f"{friendly} {state}")
            return " · ".join(parts) or "容器状态读取中…"
        except Exception:
            return "容器状态读取中…"

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
            self._set_step("docker", "running", "正在启动 Docker 引擎…")
            self.docker.ensure_ready()
            self._set_step("docker", "ok", "Docker 引擎已就绪")
            self._set_step("containers", "running", "正在准备环境…")
            self._tick_step_detail("containers", self._compose_summary)
            self.docker.compose_up()
            self._set_step("containers", "ok", "容器已创建，正在启动服务…")
            self._set_step("mysql", "running", f"正在启动 MySQL（127.0.0.1:{self.settings.mysql_port}）…")
            if not self._wait_port(
                self.settings.mysql_port,
                on_tick=lambda: self._set_step("mysql", "running", self._compose_summary()),
            ):
                raise RuntimeError("MySQL 启动超时，请检查 Docker 容器状态")
            self._set_step("mysql", "ok", "MySQL 已连接")
            milvus_port = self._milvus_port()
            self._set_step("milvus", "running", f"正在启动 Milvus（127.0.0.1:{milvus_port}）…")
            if not self._wait_port(
                milvus_port,
                on_tick=lambda: self._set_step("milvus", "running", self._compose_summary()),
            ):
                raise RuntimeError("Milvus 启动超时，请检查 Docker 容器状态")
            self._set_step("milvus", "ok", "Milvus 已连接")
            self._set_step("attu", "running", "正在启动 Attu（127.0.0.1:17003）…")
            attu_ready = self._wait_port(
                17003,
                timeout=25,
                on_tick=lambda: self._set_step("attu", "running", self._compose_summary()),
            )
            if not attu_ready:
                self._set_step("attu", "running", "Attu 启动异常，正在重试…")
                try:
                    self.docker._run([self.docker.docker, "compose", "restart", "attu"])
                except Exception:
                    pass
                attu_ready = self._wait_port(
                    17003,
                    timeout=40,
                    on_tick=lambda: self._set_step("attu", "running", self._compose_summary()),
                )
            if attu_ready:
                self._set_step("attu", "ok", "Attu 已就绪")
            else:
                self._set_step("attu", "ok", "Attu 未就绪（不影响主服务）")
            if self.server.is_running():
                self._set_step("service", "running", "检测到本地服务，正在校验…")
            else:
                self._set_step("service", "running", "正在启动本地服务…")
                self.server.start()
            try:
                if getattr(self.server, "app", None) is not None:
                    self.server.app.state.launcher_progress = self.progress
            except Exception:
                pass
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
        try:
            self._apply_exit_policy()
        except Exception:
            pass
        try:
            if getattr(self, "_progress_server", None) is not None:
                self._progress_server.stop()
        except Exception:
            pass
        try:
            self.server.stop()
        except Exception:
            pass
        self._stop_tts_worker()
        try:
            from voice.asr.local_worker import shutdown_asr_workers

            shutdown_asr_workers()
        except Exception:
            pass
        try:
            from ingestion.local_embedding.client import shutdown_embedding_workers

            shutdown_embedding_workers()
        except Exception:
            pass
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass

    def _stop_tts_worker(self) -> None:
        """显式停止 TTS 子进程，避免只依赖 uvicorn lifespan 关闭路径。"""
        try:
            app = self.server.app
            if app is not None:
                worker = getattr(app.state, "tts_worker", None)
                if worker is not None:
                    worker.stop_service()
        except Exception:
            pass

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
            try:
                self.server.stop()
            except Exception:
                pass
            self._stop_tts_worker()
            try:
                from voice.asr.local_worker import shutdown_asr_workers

                shutdown_asr_workers()
            except Exception:
                pass
            try:
                from ingestion.local_embedding.client import shutdown_embedding_workers

                shutdown_embedding_workers()
            except Exception:
                pass
            if stop_docker:
                try:
                    self.docker.compose_stop()
                except Exception:
                    pass
            self.show_launcher()

        app.state.shutdown_callback = desktop_shutdown
