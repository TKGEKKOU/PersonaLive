def test_embedding_resource_api_uses_managed_resource_manager(client, tmp_path, monkeypatch):
    from ingestion.local_embedding.resources import LocalEmbeddingResourceManager

    manager = LocalEmbeddingResourceManager(tmp_path)
    monkeypatch.setattr(client.app.state, "embedding_resources", manager, raising=False)
    monkeypatch.setattr(manager, "start_install", lambda model_id, source, device: True)

    initial = client.get("/api/embedding/status")
    install = client.post(
        "/api/embedding/install",
        headers={"X-YUMENO-Request": "web"},
        json={"model_id": "Qwen/Qwen3-Embedding-0.6B", "source": "modelscope", "device": "auto"},
    )

    assert initial.status_code == 200
    assert initial.json()["model_id"] == "Qwen/Qwen3-Embedding-0.6B"
    assert install.status_code == 202


def test_embedding_install_rejects_unsafe_model_id(client, tmp_path, monkeypatch):
    from ingestion.local_embedding.resources import LocalEmbeddingResourceManager

    manager = LocalEmbeddingResourceManager(tmp_path)
    monkeypatch.setattr(client.app.state, "embedding_resources", manager, raising=False)

    response = client.post(
        "/api/embedding/install",
        headers={"X-YUMENO-Request": "web"},
        json={"model_id": "../outside", "source": "modelscope", "device": "auto"},
    )

    assert response.status_code == 422


def test_embedding_resource_mutation_requires_same_origin_header(client):
    response = client.delete("/api/embedding/model")

    assert response.status_code == 403
