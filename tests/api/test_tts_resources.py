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
