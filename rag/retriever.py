"""RAG 检索层：Dense + BM25 混合检索与 RRF 融合。

检索是 RAG 的第一道"质量与性能"关口，本模块负责两件事：

1. 构建"作用域受限"的混合检索器
   - Dense：Milvus HNSW 索引上的向量相似度检索（IP 内积），负责语义召回；
   - Sparse：Milvus 内置 BM25 函数把原文转成稀疏向量，走 SPARSE_INVERTED_INDEX，
     负责关键词/术语的精确匹配召回；
   - 两路结果通过 RRF（Reciprocal Rank Fusion）融合排序，兼顾语义与词汇两种信号。

2. 缓存 Milvus 连接
   - MilvusRagStore 及其底层连接按"连接标识 + 集合名 + 向量维度"缓存复用；
     连接时会做一次 describe_collection 维度校验，缓存后整个进程只承担一次，
     检索热路径不再重复建连、重复校验——这是不影响召回质量的最直接提速。

作用域过滤（expr）不是相似度加分项，而是强制的数据边界：
workspace_id + knowledge_space_ids + category == "content"，保证任意角色只能
检索自己工作区内的知识片段，杜绝跨角色串数据。

注意：返回条数由 k 控制；ranker_params.k 是 RRF 排名平滑常数，不是返回条数。
"""

from __future__ import annotations

from functools import lru_cache

from settings import Settings
from ingestion.milvus_store import MilvusRagStore, quote_filter_value
from rag.contracts import RagQueryContext


@lru_cache(maxsize=4)
def _cached_store(
    store_type: type[MilvusRagStore],
    milvus_uri: str,
    milvus_user: str,
    milvus_password: str,
    collection_name: str,
    dimensions: int,
) -> object:
    """按连接标识缓存"已连接"的 MilvusRagStore。

    缓存键覆盖所有会影响连接/集合的配置项；任一配置变化都会自动生成新的
    store，避免脏复用。缓存键同时纳入 store 类本身，因此测试中的替身类
    （FakeStore）与生产类互不污染缓存。connect() 内部含一次集合维度校验，
    缓存后该开销只发生一次，检索热路径不再重复连接 Milvus。
    """

    return store_type().connect()


def build_scope_expression(context: RagQueryContext) -> str:
    """生成服务端作用域过滤表达式，保证角色之间的向量数据不可串扰。

    返回的 expr 会作为 Milvus 的过滤条件下推到检索阶段（先过滤、再相似度
    排序），而不是在召回后过滤，因此不会放大候选集。
    """

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
    """构建 Dense + BM25 sparse 混合检索器，并用 RRF 融合两路排名。

    Args:
        context: 查询上下文，用于生成强制作用域过滤（expr）。
        k: 最终返回给下游（批量评分/生成）的片段数量。调大 k 会提高召回
           上限但增加评分与生成的 token 开销；本项目默认 4 兼顾两者。

    score_threshold 只作用于 Dense 一路的初筛（去掉明显无关的向量命中），
    RRF 融合后仍是按 k 截断。注意：这里返回的是 langchain 检索器对象，
    对象本身很轻量（底层连接已被 _cached_store 缓存）。
    """

    settings = Settings.load()
    vector_store = _cached_store(
        MilvusRagStore,
        settings.milvus_uri,
        settings.milvus_user,
        settings.milvus_password,
        settings.collection_name,
        settings.embedding_dimensions,
    )
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
