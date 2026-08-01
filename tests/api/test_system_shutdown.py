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
