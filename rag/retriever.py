from ingestion.milvus_store import MilvusRagStore, quote_filter_value
from rag.contracts import RagQueryContext


def build_scope_expression(context: RagQueryContext) -> str:
    spaces = ", ".join(
        quote_filter_value(space_id) for space_id in context.knowledge_space_ids
    )
    return " and ".join(
        [
            f"workspace_id == {quote_filter_value(context.workspace_id)}",
            f"knowledge_space_id in [{spaces}]",
            'category == "content"',
        ]
    )


def build_retriever(context: RagQueryContext, k: int = 4):
    vector_store = MilvusRagStore().connect()
    return vector_store.as_retriever(
        search_type="similarity",
        search_kwargs={
            "k": k,
            "score_threshold": 0.1,
            "ranker_type": "rrf",
            "ranker_params": {"k": 100},
            "expr": build_scope_expression(context),
        },
    )
