import json


def test_settings_are_saved_outside_env_and_secrets_are_masked(client, tmp_path, monkeypatch):
    from app.routers import settings as settings_router

    settings_path = tmp_path / "data" / "local_settings.json"
    monkeypatch.setattr(settings_router, "SETTINGS_PATH", settings_path)

    current = client.get("/api/settings")
    assert current.status_code == 200
    assert current.json()["openai_api_key_configured"] is False
    assert current.json()["embedding_provider"] == "managed_local"
    assert current.json()["embedding_model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert current.json()["embedding_model_source"] == "modelscope"
    assert current.json()["embedding_device"] == "auto"
    assert current.json()["embedding_dimensions"] == 1024

    saved = client.patch(
        "/api/settings",
        json={
            "openai_api_key": "new-key",
            "openai_base_url": "https://api.deepseek.com",
            "openai_model": "deepseek-chat",
            "embedding_api_key": "embedding-key",
            "embedding_provider": "custom",
            "embedding_model_source": "huggingface",
            "embedding_device": "cpu",
            "embedding_base_url": "https://example.com/v1",
            "embedding_model": "embedding-model",
            "embedding_dimensions": 1024,
            "embedding_send_dimensions": False,
            "tavily_api_key": "tavily-key",
            "enable_web_fallback": True,
        },
    )

    assert saved.status_code == 200
    assert saved.json()["openai_api_key_configured"] is True
    assert "openai_api_key" not in saved.json()
    assert saved.json()["embedding_dimensions"] == 1024
    assert saved.json()["embedding_provider"] == "custom"
    assert saved.json()["embedding_model_source"] == "huggingface"
    assert saved.json()["embedding_device"] == "cpu"
    assert saved.json()["embedding_send_dimensions"] is False
    assert saved.json()["enable_web_fallback"] is True
    assert saved.json()["restart_required"] is False
    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert stored["openai_api_key"] == "new-key"
    assert stored["embedding_api_key"] == "embedding-key"
    assert stored["embedding_provider"] == "custom"
    assert stored["web_search_provider"] == "tavily"
    assert stored["web_search_api_key"] == "tavily-key"


def test_empty_key_fields_preserve_existing_frontend_settings(client, tmp_path, monkeypatch):
    from app.routers import settings as settings_router

    settings_path = tmp_path / "local_settings.json"
    settings_path.write_text(json.dumps({"openai_api_key": "keep-me"}), encoding="utf-8")
    monkeypatch.setattr(settings_router, "SETTINGS_PATH", settings_path)

    saved = client.patch("/api/settings", json={"openai_api_key": "", "openai_model": "new-model"})

    assert saved.status_code == 200
    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert stored["openai_api_key"] == "keep-me"
    assert stored["openai_model"] == "new-model"


def test_web_search_provider_uses_generic_key_field_and_masks_it(client, tmp_path, monkeypatch):
    from app.routers import settings as settings_router

    settings_path = tmp_path / "local_settings.json"
    monkeypatch.setattr(settings_router, "SETTINGS_PATH", settings_path)

    saved = client.patch(
        "/api/settings",
        json={
            "web_search_provider": "bocha",
            "web_search_api_key": "provider-key",
            "enable_web_fallback": True,
        },
    )

    assert saved.status_code == 200
    assert saved.json()["web_search_provider"] == "bocha"
    assert saved.json()["web_search_api_key_configured"] is True
    assert "web_search_api_key" not in saved.json()
    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert stored["web_search_provider"] == "bocha"
    assert stored["web_search_api_key"] == "provider-key"


def test_custom_web_search_provider_keeps_base_url_and_masks_key(client, tmp_path, monkeypatch):
    from app.routers import settings as settings_router

    settings_path = tmp_path / "local_settings.json"
    monkeypatch.setattr(settings_router, "SETTINGS_PATH", settings_path)

    saved = client.patch(
        "/api/settings",
        json={
            "web_search_provider": "custom",
            "web_search_base_url": "https://search.example/v1/web-search",
            "web_search_api_key": "provider-key",
        },
    )

    assert saved.status_code == 200
    assert saved.json()["web_search_provider"] == "custom"
    assert saved.json()["web_search_base_url"] == "https://search.example/v1/web-search"
    assert saved.json()["web_search_api_key_configured"] is True
    assert "web_search_api_key" not in saved.json()


def test_reset_deletes_only_frontend_provider_settings(client, tmp_path, monkeypatch):
    from app.routers import settings as settings_router

    settings_path = tmp_path / "local_settings.json"
    settings_path.write_text(json.dumps({"openai_api_key": "key", "openai_model": "model"}), encoding="utf-8")
    monkeypatch.setattr(settings_router, "SETTINGS_PATH", settings_path)

    reset = client.delete("/api/settings")

    assert reset.status_code == 200
    assert reset.json()["openai_api_key_configured"] is False
    assert reset.json()["openai_model"] == ""
    assert not settings_path.exists()


def test_saved_api_key_can_only_be_revealed_by_protected_whitelisted_endpoint(client, tmp_path, monkeypatch):
    from app.routers import settings as settings_router

    settings_path = tmp_path / "local_settings.json"
    settings_path.write_text(json.dumps({
        "openai_api_key": "openai-secret",
        "embedding_api_key": "embedding-secret",
        "web_search_api_key": "search-secret",
    }), encoding="utf-8")
    monkeypatch.setattr(settings_router, "SETTINGS_PATH", settings_path)

    masked = client.get("/api/settings")
    denied = client.post("/api/settings/reveal-key", json={"field": "openai_api_key"})
    revealed = client.post(
        "/api/settings/reveal-key",
        headers={"X-YUMENO-Request": "web"},
        json={"field": "embedding_api_key"},
    )
    invalid = client.post(
        "/api/settings/reveal-key",
        headers={"X-YUMENO-Request": "web"},
        json={"field": "unknown_field"},
    )

    assert "openai_api_key" not in masked.json()
    assert denied.status_code == 403
    assert revealed.status_code == 200
    assert revealed.json() == {"value": "embedding-secret"}
    assert revealed.headers["cache-control"] == "no-store"
    assert invalid.status_code == 422
