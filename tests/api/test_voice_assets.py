from pathlib import Path

from voice.gpt_sovits.config import GPTSoVITSConfig


def test_voice_asset_crud(client, tmp_path):
    from app.models import VoiceAsset

    client.app.state.tts_resources.project_root = tmp_path
    headers = {"X-YUMENO-Request": "web"}

    created = client.post("/api/voice-assets", json={"name": "测试音色"}, headers=headers)
    assert created.status_code == 201
    asset_id = created.json()["id"]
    assert created.json()["status"] == "created"

    listed = client.get("/api/voice-assets")
    assert listed.status_code == 200
    assert any(item["id"] == asset_id for item in listed.json()["items"])

    updated = client.patch(
        f"/api/voice-assets/{asset_id}",
        json={"gpt_weights_path": "D:/x.ckpt", "sovits_weights_path": "D:/x.pth"},
        headers=headers,
    )
    assert updated.json()["gpt_weights_path"] == "D:/x.ckpt"

    deleted = client.delete(f"/api/voice-assets/{asset_id}", headers=headers)
    assert deleted.status_code == 200
    assert client.get(f"/api/voice-assets/{asset_id}").status_code == 404


def test_voice_asset_import_scans_directory(client, tmp_path):
    from app.models import VoiceAsset

    client.app.state.tts_resources.project_root = tmp_path
    model_dir = tmp_path / "models"
    char = model_dir / "角色A"
    char.mkdir(parents=True)
    (char / "角色A.ckpt").write_bytes(b"g")
    (char / "角色A.pth").write_bytes(b"s")
    (char / "ref.wav").write_bytes(b"w")

    response = client.post(
        "/api/voice-assets/import",
        json={"directory": str(model_dir)},
        headers={"X-YUMENO-Request": "web"},
    )

    assert response.status_code == 200
    items = response.json()["imported"]
    assert len(items) == 1
    assert items[0]["status"] == "ready"
    assert items[0]["refer_audio_path"].endswith("ref.wav")


def test_gpt_sovits_status_reports_config(client, tmp_path):
    config = GPTSoVITSConfig(tmp_path)
    client.app.state.gpt_sovits_config = config
    from voice.gpt_sovits.adapter import GPTSoVITSAdapter

    client.app.state.gpt_sovits = GPTSoVITSAdapter(config, tmp_path)

    response = client.get("/api/gpt-sovits/status")

    assert response.status_code == 200
    assert response.json()["configured"] is False
    assert response.json()["installed"] is False
