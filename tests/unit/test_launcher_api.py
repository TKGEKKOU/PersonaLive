from pathlib import Path

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
            {"app_port": 17000, "mysql_port": 17001, "milvus_uri": "http://127.0.0.1:17002"},
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


def test_start_boots_server_and_registers_shutdown_callback(tmp_path: Path):
    server = FakeServer(False)
    api = LauncherApi(tmp_path, FakeDocker(True), server)
    result = api.start()
    assert result["ok"] is True
    assert server.started is True
    assert callable(server.app.state.shutdown_callback)


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
