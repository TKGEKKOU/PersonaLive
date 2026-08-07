import time

from app.routers import eval as eval_router


def _create_persona(client):
    response = client.post("/api/personas", json={"name": "Alpha"})
    assert response.status_code == 201
    return response.json()


def test_eval_run_status_and_results_flow(client, monkeypatch):
    persona = _create_persona(client)

    def fake_execute(payload, session_factory, job):
        job["state"] = "done"
        job["progress"] = 1
        job["total"] = 1
        job["metrics"] = {"recall_at_k": 1.0, "grounded_rate": 0.8, "cases_checked": 1}
        job["cases"] = [{"question": "q1"}]

    monkeypatch.setattr(eval_router, "_execute", fake_execute)
    response = client.post("/api/eval/run", json={"persona_id": persona["id"]})
    assert response.status_code == 202

    state = "pending"
    for _ in range(100):
        status = client.get("/api/eval/status").json()
        state = status["state"]
        if state in {"done", "error"}:
            break
        time.sleep(0.02)
    assert state == "done"

    results = client.get("/api/eval/results").json()
    assert results["metrics"]["recall_at_k"] == 1.0
    assert results["cases"][0]["question"] == "q1"


def test_eval_run_rejects_second_concurrent_task(client):
    client.app.state.eval_job = {"state": "running"}
    response = client.post("/api/eval/run", json={"persona_id": "p1"})
    assert response.status_code == 409


def test_eval_run_accepts_tier_payload(client, monkeypatch):
    persona = _create_persona(client)
    captured = {}

    def fake_execute(payload, session_factory, job):
        captured["payload"] = payload
        job["state"] = "done"
        job["phase"] = "done"
        job["progress"] = 5
        job["total"] = 5
        job["metrics"] = {"recall_at_k": 1.0}
        job["cases"] = []

    monkeypatch.setattr(eval_router, "_execute", fake_execute)
    response = client.post(
        "/api/eval/run",
        json={"persona_id": persona["id"], "tier": "standard"},
    )
    assert response.status_code == 202
    assert captured["payload"].tier == "standard"


def test_eval_analyze_requires_completed_job(client):
    client.app.state.eval_job = {"state": "pending"}
    response = client.post("/api/eval/analyze")
    assert response.status_code == 409


def test_eval_analyze_returns_analysis(client, monkeypatch):
    client.app.state.eval_job = {
        "state": "done",
        "metrics": {"cases_total": 5},
        "cases": [],
    }
    monkeypatch.setattr(
        "rag.eval.analyzer.analyze_results",
        lambda metrics, cases: "分析结论",
    )
    response = client.post("/api/eval/analyze")
    assert response.status_code == 200
    assert response.json()["analysis"] == "分析结论"
