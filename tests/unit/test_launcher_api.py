from pathlib import Path
import time

from desktop.launcher_api import LauncherApi


class FakeDocker:
    def __init__(self, ready=True):
        self.ready = ready
        self.docker = "docker"
        self.actions = []

    def is_ready(self):
        return self.ready

    def _run(self, command):
        return type("R", (), {"returncode": 0, "stdout": "abc\n"})()

    def ensure_ready(self):
        pass

    def compose_up(self):
        pass

    def compose_stop(self):
        self.actions.append("stop")

    def compose_down(self):
        self.actions.append("down")


class FakeServer:
    def __init__(self, running=False):
        self.running = running
        self.settings = type(
            "S",
            (),
            {"app_port": 17000, "milvus_uri": "http://127.0.0.1:17002"},
        )()
        self.url = "http://127.0.0.1:17000"
        self.app = None
        self.started = False

    def is_running(self):
        return self.running

    def start(self):
        self.started = True
        self.app = type("A", (), {})()
        self.app.state = type("St", (), {})()

    def stop(self):
        self.started = False


def test_status_reports_components(tmp_path: Path):
    api = LauncherApi(tmp_path, FakeDocker(True), FakeServer(False))
    status = api.status()
    assert status["docker_ready"] is True
    assert status["containers_up"] is True
    assert status["service_running"] is False
    assert status["port"] == 17000


def test_running_detail_refresh_does_not_reset_elapsed(tmp_path: Path):
    api = LauncherApi(tmp_path, FakeDocker(True), FakeServer(False))
    api._set_step("containers", "running", "正在准备环境…")
    first = api._step_started["containers"]
    # 模拟容器状态定时刷新：仅更新 detail，不得重置计时
    api._set_step("containers", "running", "MySQL 启动中 · etcd 启动中 · MinIO 启动中")
    assert api._step_started["containers"] == first
    api._set_step("containers", "ok", "容器已创建")
    assert "containers" not in api._step_started


def test_start_boots_server_and_registers_shutdown_callback(tmp_path: Path):
    server = FakeServer(False)
    api = LauncherApi(tmp_path, FakeDocker(True), server)
    api._wait_port = lambda port, timeout=120, on_tick=None: True  # fake ports as ready
    api._wait_http = lambda timeout=15: True  # fake service health as ready
    result = api.start()
    assert result["ok"] is True
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not api.progress()["done"]:
        time.sleep(0.02)
    progress = api.progress()
    assert progress["done"] is True
    assert progress["ok"] is True
    assert all(step["state"] == "ok" for step in progress["steps"])
    assert server.started is True
    assert callable(server.app.state.shutdown_callback)


def test_do_exit_cleans_up_even_when_step_throws(tmp_path, monkeypatch):
    """do_exit 任一步抛错时，后续清理与窗口销毁仍必须执行。"""

    from desktop.launcher_api import LauncherApi

    server = FakeServer(True)
    api = LauncherApi(tmp_path, FakeDocker(True), server)
    calls: list[str] = []
    destroyed: list[bool] = []

    def boom():
        raise RuntimeError("boom")

    class FakeWindow:
        def destroy(self):
            destroyed.append(True)

    monkeypatch.setattr(api, "_apply_exit_policy", boom)
    monkeypatch.setattr(api, "server", type("S", (), {"stop": boom})())
    monkeypatch.setattr(api, "_stop_tts_worker", lambda: calls.append("tts"))
    monkeypatch.setattr(
        "voice.asr.local_worker.shutdown_asr_workers", lambda: calls.append("asr")
    )
    monkeypatch.setattr(
        "ingestion.local_embedding.client.shutdown_embedding_workers",
        lambda: calls.append("embedding"),
    )
    api._window = FakeWindow()

    api.do_exit()

    assert calls == ["tts", "asr", "embedding"]
    assert destroyed == [True]


def test_do_exit_pause_policy_stops_containers(tmp_path: Path):
    docker = FakeDocker(True)
    server = FakeServer(True)
    api = LauncherApi(tmp_path, docker, server)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "docker_settings.json").write_text('{"on_exit": "pause"}', encoding="utf-8")
    api._window = type("W", (), {"destroy": lambda self: None})()
    api.do_exit()
    assert docker.actions == ["stop"]
    assert api._exiting is True


def test_do_exit_remove_policy_runs_down(tmp_path: Path):
    docker = FakeDocker(True)
    server = FakeServer(True)
    api = LauncherApi(tmp_path, docker, server)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "docker_settings.json").write_text('{"on_exit": "remove"}', encoding="utf-8")
    api._window = type("W", (), {"destroy": lambda self: None})()
    api.do_exit()
    assert docker.actions == ["down"]


def test_on_closing_blocks_until_exit_confirmed(tmp_path: Path):
    api = LauncherApi(tmp_path, FakeDocker(True), FakeServer(True))
    assert api.on_closing() is False
    api._exiting = True
    assert api.on_closing() is True
