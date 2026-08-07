from dataclasses import replace

from ingestion import embeddings
from settings import Settings


def test_managed_local_provider_uses_local_adapter(tmp_path, monkeypatch):
    settings = replace(
        Settings.load(tmp_path),
        embedding_provider="managed_local",
        embedding_model="Qwen/Qwen3-Embedding-0.6B",
        embedding_device="auto",
    )
    marker = object()
    monkeypatch.setattr(embeddings, "get_managed_embeddings", lambda *args: marker)

    assert embeddings.get_embedding_model(settings) is marker


def test_cloud_provider_keeps_openai_embeddings_factory(tmp_path, monkeypatch):
    settings = replace(
        Settings.load(tmp_path),
        embedding_provider="qwen",
        embedding_model="text-embedding-v4",
        embedding_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
    marker = object()
    monkeypatch.setattr(embeddings, "_build_embedding_model", lambda *args: marker)

    assert embeddings.get_embedding_model(settings) is marker


def test_shutdown_embedding_workers_closes_all_instances(monkeypatch):
    from ingestion.local_embedding import client

    closed: list[str] = []

    class FakeEmbeddings:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)

    client._EMBEDDING_INSTANCES.extend(
        [FakeEmbeddings("a"), FakeEmbeddings("b")]
    )
    try:
        client.shutdown_embedding_workers()
        assert closed == ["a", "b"]
    finally:
        client._EMBEDDING_INSTANCES.clear()
def test_warm_managed_embedding_runs_one_probe(monkeypatch, tmp_path):
    from dataclasses import replace

    import ingestion.embeddings as embeddings_module
    from settings import Settings

    calls = []

    class FakeEmbedding:
        def embed_query(self, text):
            calls.append(text)
            return [0.0]

    settings = replace(Settings.load(tmp_path), embedding_provider="managed_local")
    monkeypatch.setattr(embeddings_module, "get_embedding_model", lambda active: FakeEmbedding())

    assert embeddings_module.warm_managed_embedding(settings) is True
    assert calls == ["模型预热"]
