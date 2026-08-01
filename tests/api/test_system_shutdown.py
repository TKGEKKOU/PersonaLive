def test_shutdown_schedules_exit_without_killing_tests(client, monkeypatch):
    from app.routers import system as system_router

    calls = []
    monkeypatch.setattr(system_router.os, "_exit", lambda code: calls.append(code))

    class FakeThread:
        def __init__(self, target, daemon=False, name=""):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(system_router.threading, "Thread", FakeThread)

    response = client.post("/api/system/shutdown")
    assert response.status_code == 200
    assert response.json() == {"status": "stopping"}
    assert calls == [0]


def test_shutdown_with_stop_docker_runs_compose_down(client, monkeypatch):
    from app.routers import system as system_router

    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return None

    monkeypatch.setattr(system_router.subprocess, "run", fake_run)
    monkeypatch.setattr(system_router.os, "_exit", lambda code: None)

    class FakeThread:
        def __init__(self, target, daemon=False, name=""):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(system_router.threading, "Thread", FakeThread)

    response = client.post("/api/system/shutdown", json={"stop_docker": True})
    assert response.status_code == 200
    assert commands and commands[0] == ["docker", "compose", "stop"]
