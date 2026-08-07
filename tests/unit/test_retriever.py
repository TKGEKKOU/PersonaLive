from rag.contracts import RagQueryContext
from rag.retriever import build_retriever, build_scope_expression


def test_retrieval_filter_contains_only_server_scope():
    context = RagQueryContext(
        persona_id="persona-a",
        workspace_id="local-default",
        knowledge_space_ids=("space-a",),
    )

    expression = build_scope_expression(context)

    assert 'workspace_id == "local-default"' in expression
    assert 'knowledge_space_id in ["space-a"]' in expression
    assert 'category == "content"' in expression


def test_query_context_requires_a_knowledge_space():
    try:
        RagQueryContext("persona-a", "local-default", ())
    except ValueError as exc:
        assert str(exc) == "knowledge_space_ids must not be empty"
    else:
        raise AssertionError("empty knowledge scope must be rejected")


def test_retriever_keeps_hybrid_rrf_configuration(monkeypatch):
    captured = {}

    class FakeVectorStore:
        def as_retriever(self, **kwargs):
            captured.update(kwargs)
            return "retriever"

    class FakeStore:
        def connect(self):
            return FakeVectorStore()

    monkeypatch.setattr("rag.retriever.MilvusRagStore", FakeStore)
    context = RagQueryContext("persona-a", "local-default", ("space-a",))

    assert build_retriever(context, k=6) == "retriever"
    assert captured["search_type"] == "similarity"
    assert captured["search_kwargs"] == {
        "k": 6,
        "score_threshold": 0.1,
        "ranker_type": "rrf",
        "ranker_params": {"k": 100},
        "expr": build_scope_expression(context),
    }
