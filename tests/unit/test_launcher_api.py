from pathlib import Path

from desktop.launcher_api import LauncherApi


class FakeDocker:
    def __init__(self, ready=True):
        self.ready = ready
        self.docker = "docker"

    def is_ready(self):
        return self.ready

    def _run(self, command):
        return type("R", (), {"returncode": 0, "stdout": "abc\n"})()

    def ensure_ready(self):
        pass

    def compose_up(self):
        pass


class FakeServer:
    def __init__(self, running=False):
        self.running = running
        self.settings = type("S", (), {"app_port": 17000})()
        self.url = "http://127.0.0.1:17000"
        self.app = None
        self.started = False

    def is_running(self):
        return self.running

    def start(self):
        self.started = True
        self.app = type("A", (), {})()
        self.app.state = type("St", (), {})()


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
