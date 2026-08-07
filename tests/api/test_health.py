from fastapi.testclient import TestClient

from app.main import create_app


def test_health_is_available_without_login():
    with TestClient(create_app(initialize_database=False)) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "workspace_id": "local-default"}
