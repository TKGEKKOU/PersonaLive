import json


def test_get_integrations_returns_defaults(client, tmp_path, monkeypatch):
    from app.routers import integrations as integrations_router

    path = tmp_path / "data" / "integrations.json"
    monkeypatch.setattr(integrations_router, "INTEGRATIONS_PATH", path)
    response = client.get("/api/integrations")
    assert response.status_code == 200
    body = response.json()
    assert body["onebot11"]["enabled"] is False
    assert body["onebot11"]["access_token_configured"] is False
    assert body["onebot11"]["group_trigger"] == "at"
    assert body["onebot11"]["connected"] is False
    assert body["onebot11"]["client_count"] == 0


def test_put_integrations_persists(client, tmp_path, monkeypatch):
    from app.routers import integrations as integrations_router

    path = tmp_path / "data" / "integrations.json"
    monkeypatch.setattr(integrations_router, "INTEGRATIONS_PATH", path)
    response = client.put(
        "/api/integrations/onebot11",
        json={"enabled": True, "access_token": "secret-token",
              "group_trigger": "prefix", "prefix": "机器人，", "default_persona_id": "p1"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["access_token_configured"] is True
    assert "secret-token" not in response.text
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["onebot11"]["access_token"] == "secret-token"
    assert saved["onebot11"]["group_trigger"] == "prefix"


def test_put_integrations_rejects_invalid_trigger(client, tmp_path, monkeypatch):
    from app.routers import integrations as integrations_router

    path = tmp_path / "data" / "integrations.json"
    monkeypatch.setattr(integrations_router, "INTEGRATIONS_PATH", path)
    response = client.put("/api/integrations/onebot11", json={"group_trigger": "bogus"})
    assert response.status_code == 422
