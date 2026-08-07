def test_status_reports_components_without_hiding_failures(client, monkeypatch):
    monkeypatch.setattr(
        "app.main.get_system_status",
        lambda: {"sqlite": "ok", "milvus": "unavailable"},
    )

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {"sqlite": "ok", "milvus": "unavailable"}
