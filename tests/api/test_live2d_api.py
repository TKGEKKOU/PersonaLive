from pathlib import Path

from app.routers.live2d import discover_models


def test_live2d_models_endpoint(client):
    response = client.get("/api/live2d/models")
    assert response.status_code == 200
    payload = response.json()
    assert "models" in payload
    for model in payload["models"]:
        assert model["id"]
        assert model["entry"].endswith((".model.json", ".model3.json"))
        assert model["kind"] in {"cubism2", "cubism4"}


def test_discover_models_prefers_cubism4(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "A.model.json").write_text("{}", encoding="utf-8")
    (tmp_path / "a" / "A.model3.json").write_text("{}", encoding="utf-8")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "B.model.json").write_text("{}", encoding="utf-8")
    (tmp_path / "c").mkdir()  # no entry file, must be skipped

    models = discover_models(tmp_path)
    assert [model["id"] for model in models] == ["a", "b"]
    assert models[0]["kind"] == "cubism4"
    assert models[0]["entry"] == "a/A.model3.json"
    assert models[1]["kind"] == "cubism2"
    assert models[1]["entry"] == "b/B.model.json"
