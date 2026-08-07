from rag.service import RagResult


def _create_persona(client):
    response = client.post("/api/personas", json={"name": "Alpha"})
    assert response.status_code == 201
    return response.json()


def test_query_scope_is_derived_from_path_persona(client, monkeypatch):
    persona = _create_persona(client)
    captured = {}

    def fake_query(request):
        captured["request"] = request
        return RagResult.empty("No indexed evidence")

    monkeypatch.setattr("app.routers.rag.rag_service.query", fake_query)
    response = client.post(
        f"/api/personas/{persona['id']}/rag/query",
        json={"question": "What is in my material?"},
    )

    assert response.status_code == 200
    assert captured["request"].context.knowledge_space_ids == (
        persona["knowledge_space_id"],
    )
    assert captured["request"].persona_name == "Alpha"


def test_query_passes_persona_profile_to_chat_service(client, monkeypatch):
    persona = client.post(
        "/api/personas",
        json={"name": "爱弥斯", "profile": {"description": "活泼俏皮的数据幽灵"}},
    ).json()
    captured = {}

    def fake_query(request):
        captured["request"] = request
        return RagResult.empty("ok")

    monkeypatch.setattr("app.routers.rag.rag_service.query", fake_query)
    response = client.post(
        f"/api/personas/{persona['id']}/rag/query",
        json={"question": "你好"},
    )

    assert response.status_code == 200
    assert captured["request"].persona_profile["description"] == "活泼俏皮的数据幽灵"


def test_query_rejects_client_scope_override(client):
    persona = _create_persona(client)
    response = client.post(
        f"/api/personas/{persona['id']}/rag/query",
        json={"question": "facts", "knowledge_space_ids": ["space-b"]},
    )

    assert response.status_code == 422
