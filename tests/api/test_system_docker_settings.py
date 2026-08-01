def test_docker_settings_defaults_to_keep(client, tmp_path, monkeypatch):
    from app.routers import system as system_router

    path = tmp_path / "docker_settings.json"
    monkeypatch.setattr(system_router, "DOCKER_SETTINGS_PATH", path)
    response = client.get("/api/system/docker-settings")
    assert response.status_code == 200
    assert response.json() == {"on_exit": "keep"}


def test_docker_settings_update_persists(client, tmp_path, monkeypatch):
    from app.routers import system as system_router

    path = tmp_path / "docker_settings.json"
    monkeypatch.setattr(system_router, "DOCKER_SETTINGS_PATH", path)
    response = client.put("/api/system/docker-settings", json={"on_exit": "pause"})
    assert response.status_code == 200
    assert response.json() == {"on_exit": "pause"}
    assert path.read_text(encoding="utf-8") == '{\n  "on_exit": "pause"\n}\n'


def test_docker_settings_rejects_invalid_policy(client, tmp_path, monkeypatch):
    from app.routers import system as system_router

    path = tmp_path / "docker_settings.json"
    monkeypatch.setattr(system_router, "DOCKER_SETTINGS_PATH", path)
    response = client.put("/api/system/docker-settings", json={"on_exit": "bogus"})
    assert response.status_code == 422


def test_docker_pause_and_remove_run_compose(client, monkeypatch):
    from app.routers import system as system_router

    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(system_router.subprocess, "run", fake_run)

    pause = client.post("/api/system/docker/pause")
    assert pause.status_code == 200
    assert pause.json() == {"ok": True}
    remove = client.post("/api/system/docker/remove")
    assert remove.status_code == 200
    assert remove.json() == {"ok": True}
    assert commands == [["docker", "compose", "stop"], ["docker", "compose", "down"]]
