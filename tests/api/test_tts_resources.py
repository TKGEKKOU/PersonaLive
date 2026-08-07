from voice.tts.install import TTSResourceManager


def test_tts_status_is_local_and_available(client, tmp_path):
    client.app.state.tts_resources = TTSResourceManager(tmp_path)
    response = client.get("/api/tts/status")

    assert response.status_code == 200
    assert response.json()["download_size"].startswith("约 2.2 GB")


def test_tts_config_requires_same_origin_header(client, tmp_path):
    client.app.state.tts_resources = TTSResourceManager(tmp_path)
    denied = client.patch("/api/tts/config", json={"enabled": False})
    accepted = client.patch(
        "/api/tts/config",
        json={"enabled": False},
        headers={"X-YUMENO-Request": "web"},
    )

    assert denied.status_code == 403
    assert accepted.status_code == 200
    assert accepted.json()["enabled"] is False


def test_tts_config_can_switch_engine(client, tmp_path, monkeypatch):
    manager = TTSResourceManager(tmp_path)
    client.app.state.tts_resources = manager
    stopped = []

    class FakeWorker:
        use_gpu = True

        def stop_service(self):
            stopped.append(True)

    client.app.state.tts_worker = FakeWorker()

    response = client.patch(
        "/api/tts/config",
        json={"engine": "gpt_sovits"},
        headers={"X-YUMENO-Request": "web"},
    )

    assert response.status_code == 200
    assert response.json()["engine"] == "gpt_sovits"
    assert stopped == [True]


def test_tts_install_accepts_request_when_lunar_runtime_is_bundled(client, tmp_path, monkeypatch):
    manager = TTSResourceManager(tmp_path)
    manager.runtime_dir.mkdir(parents=True)
    manager.runtime_path.write_bytes(b"exe")
    manager.runtime_dll_path.write_bytes(b"dll")
    manager.model_dir.mkdir(parents=True)
    manager.model_path.write_bytes(b"model")
    manager.tokenizer_path.write_bytes(b"tokenizer")
    client.app.state.tts_resources = manager
    started = []
    monkeypatch.setattr(manager, "start_install", lambda: started.append(True) or True)

    response = client.post("/api/tts/install", headers={"X-YUMENO-Request": "web"})

    assert response.status_code == 202
    assert started == [True]
    assert response.json()["runtime_bundled"] is True


def test_tts_install_can_be_cancelled(client, tmp_path, monkeypatch):
    manager = TTSResourceManager(tmp_path)
    client.app.state.tts_resources = manager
    monkeypatch.setattr(manager, "cancel_install", lambda: True)

    response = client.delete("/api/tts/install/cancel", headers={"X-YUMENO-Request": "web"})

    assert response.status_code == 202
