from voice.tts.install import TTSResourceManager


def test_tts_status_is_local_and_available(client, tmp_path):
    client.app.state.tts_resources = TTSResourceManager(tmp_path)
    response = client.get("/api/tts/status")

    assert response.status_code == 200
    assert response.json()["download_size"] == "约 3 GB"


def test_tts_config_requires_same_origin_header(client, tmp_path):
    client.app.state.tts_resources = TTSResourceManager(tmp_path)
    denied = client.patch("/api/tts/config", json={"enabled": False})
    accepted = client.patch(
        "/api/tts/config",
        json={"enabled": False},
        headers={"X-PersonaLive-Request": "web"},
    )

    assert denied.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["enabled"] is False


def test_tts_install_accepts_request_when_lunar_runtime_is_bundled(client, tmp_path, monkeypatch):
    manager = TTSResourceManager(tmp_path)
    manager.runtime_dir.mkdir(parents=True)
    manager.runtime_path.write_bytes(b"exe")
    manager.runtime_dll_path.write_bytes(b"dll")
    client.app.state.tts_resources = manager
    started = []
    monkeypatch.setattr(manager, "start_install", lambda: started.append(True) or True)

    response = client.post("/api/tts/install", headers={"X-PersonaLive-Request": "web"})

    assert response.status_code == 202
    assert started == [True]
    assert response.json()["runtime_bundled"] is True
