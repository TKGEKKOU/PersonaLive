def test_worker_environment_forces_utf8(monkeypatch):
    from ingestion.local_embedding.client import worker_environment

    monkeypatch.setenv("PERSONALIVE_TEST_ENV", "preserved")

    environment = worker_environment()

    assert environment["PERSONALIVE_TEST_ENV"] == "preserved"
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert environment["PYTHONUTF8"] == "1"
