from ingestion.markdown_parser import DocumentScope
from ingestion.milvus_store import MilvusRagStore, document_filter


def test_document_filter_contains_complete_server_scope():
    scope = DocumentScope("local-default", "space-a", "doc-a")

    expression = document_filter(scope, scope.document_id)

    assert 'workspace_id == "local-default"' in expression
    assert 'knowledge_space_id == "space-a"' in expression
    assert 'document_id == "doc-a"' in expression


def test_hash_query_and_delete_use_the_same_scoped_filter(monkeypatch):
    calls = []

    class FakeClient:
        def query(self, **kwargs):
            calls.append(("query", kwargs))
            return [{"source_hash": "hash-a"}]

        def delete(self, **kwargs):
            calls.append(("delete", kwargs))

    store = MilvusRagStore()
    monkeypatch.setattr(store, "collection_exists", lambda: True)
    monkeypatch.setattr(store, "client", FakeClient)
    scope = DocumentScope("local-default", "space-a", "doc-a")

    assert store.indexed_hashes(scope, "doc-a") == {"hash-a"}
    store.delete_document(scope, "doc-a")

    assert calls[0][1]["filter"] == calls[1][1]["filter"]
    assert 'knowledge_space_id == "space-a"' in calls[0][1]["filter"]
