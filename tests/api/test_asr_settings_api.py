def test_asr_status_and_config_are_local_api(client, tmp_path, monkeypatch):
    from voice.asr.install import ASRResourceManager

    manager = ASRResourceManager(tmp_path)
    monkeypatch.setattr(client.app.state, "asr_resources", manager, raising=False)

    initial = client.get("/api/asr/status")
    updated = client.patch(
        "/api/asr/config",
        headers={"X-PersonaLive-Request": "web"},
        json={"enabled": False, "model_path": ""},
    )

    assert initial.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
