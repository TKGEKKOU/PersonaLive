from ingestion.milvus_store import MilvusRagStore, quote_filter_value
from rag.contracts import RagQueryContext


def build_scope_expression(context: RagQueryContext) -> str:
    """生成服务端作用域过滤式，保证角色之间的向量数据不可串读。"""

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
    """构建 Dense + BM25 sparse 混合检索器，并以 RRF 融合两路排名。"""

    vector_store = MilvusRagStore().connect()
    return vector_store.as_retriever(
        search_type="similarity",
        search_kwargs={
            "k": k,
            "score_threshold": 0.1,
            "ranker_type": "rrf",
            # RRF k 是排名平滑常数，不是返回文档数量；返回数量由上面的 k 控制。
            "ranker_params": {"k": 100},
            # expr 是强制数据边界，而不是相关性优化项，任何查询都必须携带。
            "expr": build_scope_expression(context),
        },
    )
