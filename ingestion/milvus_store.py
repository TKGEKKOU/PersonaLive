import json
from collections.abc import Iterable
from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_milvus import BM25BuiltInFunction, Milvus
from pymilvus import Function, IndexType, MilvusClient
from pymilvus.client.types import DataType, FunctionType, MetricType

from settings import Settings
from ingestion.embeddings import get_embedding_model
from ingestion.markdown_parser import DocumentScope


@dataclass(frozen=True)
class KnowledgeSpaceScope:
    workspace_id: str
    knowledge_space_id: str


def quote_filter_value(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def document_filter(scope: DocumentScope, document_id: str) -> str:
    return " and ".join(
        [
            f"workspace_id == {quote_filter_value(scope.workspace_id)}",
            f"knowledge_space_id == {quote_filter_value(scope.knowledge_space_id)}",
            f"document_id == {quote_filter_value(document_id)}",
        ]
    )


def knowledge_space_filter(scope: KnowledgeSpaceScope) -> str:
    return " and ".join(
        [
            f"workspace_id == {quote_filter_value(scope.workspace_id)}",
            f"knowledge_space_id == {quote_filter_value(scope.knowledge_space_id)}",
        ]
    )


"""Milvus 存储层：集合结构、写入、删除与连接。

集合设计（见 create_collection）：
- text：VARCHAR + jieba 中文分词（cnalphanumonly 过滤），作为 BM25 稀疏向量
  的输入字段；BM25 由 Milvus 内置函数（FunctionType.BM25）在写入时自动生成。
- dense：FLOAT_VECTOR，HNSW 索引（IP 内积），负责语义检索。
- sparse：SPARSE_FLOAT_VECTOR，SPARSE_INVERTED_INDEX（DAAT_MAXSCORE），
  负责关键词精确匹配；两路在检索时由 RRF 融合（见 rag/retriever.py）。
- 标量字段：source/filename/title/section 等用于展示与引用；
  workspace_id / knowledge_space_id / document_id / source_hash 用于
  强制数据隔离、增量去重与按文档删除。
- category == "content" 区分知识正文与其他元数据行，检索过滤表达式会强制带上。

一致性：写入后显式 flush（add_documents），保证随后的检索立即可见；
检索连接按配置缓存复用（rag/retriever.py 的 _cached_store），避免热路径重复建连。
"""
class MilvusRagStore:
    """Milvus adapter retaining dense, BM25 sparse, and RRF retrieval support."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or Settings.load()
        self.vector_store: Milvus | None = None

    def connection_args(self) -> dict[str, str]:
        args = {"uri": self.settings.milvus_uri}
        if self.settings.milvus_user and self.settings.milvus_password:
            args.update(
                {
                    "user": self.settings.milvus_user,
                    "password": self.settings.milvus_password,
                }
            )
        return args

    def client(self) -> MilvusClient:
        return MilvusClient(**self.connection_args())

    def collection_exists(self) -> bool:
        return self.settings.collection_name in self.client().list_collections()

    def validate_collection_dimensions(self, client: MilvusClient | None = None) -> None:
        active_client = client or self.client()
        if self.settings.collection_name not in active_client.list_collections():
            return
        description = active_client.describe_collection(collection_name=self.settings.collection_name)
        dense = next((field for field in description.get("fields", []) if field.get("name") == "dense"), None)
        params = dense.get("params", {}) if dense else {}
        actual = int(params.get("dim") or dense.get("dim") or 0) if dense else 0
        if actual and actual != self.settings.embedding_dimensions:
            raise RuntimeError(
                f"当前 Embedding 为 {self.settings.embedding_dimensions} 维，但 Milvus Collection 为 {actual} 维；"
                "请重建 Collection 并重新导入资料"
            )

    def create_collection(self, reset: bool = False) -> None:
        client = self.client()
        collection_name = self.settings.collection_name
        if collection_name in client.list_collections():
            if not reset:
                self.validate_collection_dimensions(client)
                return
            client.drop_collection(collection_name=collection_name)

        schema = client.create_schema()
        schema.add_field(
            field_name="id", datatype=DataType.INT64, is_primary=True, auto_id=True
        )
        schema.add_field(
            field_name="text",
            datatype=DataType.VARCHAR,
            max_length=12000,
            enable_analyzer=True,
            analyzer_params={"tokenizer": "jieba", "filter": ["cnalphanumonly"]},
        )
        for name, length in (
            ("source", 2000),
            ("filename", 1000),
            ("filetype", 200),
            ("title", 1000),
            ("section", 2000),
            ("category", 200),
            ("doc_id", 1000),
            ("chunk_id", 1000),
            ("source_hash", 128),
            ("workspace_id", 64),
            ("knowledge_space_id", 64),
            ("document_id", 64),
        ):
            schema.add_field(
                field_name=name, datatype=DataType.VARCHAR, max_length=length
            )
        schema.add_field(field_name="sparse", datatype=DataType.SPARSE_FLOAT_VECTOR)
        schema.add_field(
            field_name="dense",
            datatype=DataType.FLOAT_VECTOR,
            dim=self.settings.embedding_dimensions,
        )
        schema.add_function(
            Function(
                name="text_bm25_emb",
                input_field_names=["text"],
                output_field_names=["sparse"],
                function_type=FunctionType.BM25,
            )
        )

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name="sparse",
            index_name="sparse_bm25_index",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="BM25",
            params={
                "inverted_index_algo": "DAAT_MAXSCORE",
                "bm25_k1": 1.2,
                "bm25_b": 0.75,
            },
        )
        index_params.add_index(
            field_name="dense",
            index_name="dense_hnsw_index",
            index_type=IndexType.HNSW,
            metric_type=MetricType.IP,
            params={"M": 16, "efConstruction": 64},
        )
        client.create_collection(
            collection_name=collection_name,
            schema=schema,
            index_params=index_params,
        )

    # 构建 langchain_milvus 包装对象。连接本身懒建立，但对象初始化含一次
    # describe_collection 维度校验；调用方应通过 rag/retriever.py 的缓存
    # 复用本 store，避免每个查询都重建。
    def connect(self) -> Milvus:
        self.validate_collection_dimensions()
        self.vector_store = Milvus(
            embedding_function=get_embedding_model(self.settings),
            collection_name=self.settings.collection_name,
            builtin_function=BM25BuiltInFunction(),
            vector_field=["dense", "sparse"],
            consistency_level="Strong",
            auto_id=True,
            connection_args=self.connection_args(),
        )
        return self.vector_store

    # 写入后立即 flush：BM25 稀疏向量由 Milvus 内置函数异步生成，flush 保证
    # 索引任务结束后第一次检索能同时看到 dense 与 sparse 两路数据。
    def add_documents(self, documents: Iterable[Document]) -> None:
        if self.vector_store is None:
            self.connect()
        assert self.vector_store is not None
        self.vector_store.add_documents(list(documents))
        # A new scoped retriever uses a separate Milvus connection. Flush here so
        # the first query after an indexing job can observe both dense and BM25 data.
        self.client().flush(collection_name=self.settings.collection_name, timeout=10)

    def indexed_hashes(self, scope: DocumentScope, document_id: str) -> set[str]:
        if not self.collection_exists():
            return set()
        rows = self.client().query(
            collection_name=self.settings.collection_name,
            filter=document_filter(scope, document_id),
            output_fields=["source_hash"],
            limit=16384,
        )
        return {
            str(row["source_hash"])
            for row in rows
            if row.get("source_hash")
        }

    def delete_document(self, scope: DocumentScope, document_id: str) -> None:
        if not self.collection_exists():
            return
        self.client().delete(
            collection_name=self.settings.collection_name,
            filter=document_filter(scope, document_id),
        )

    def delete_knowledge_space(self, scope: KnowledgeSpaceScope) -> None:
        if not self.collection_exists():
            return
        self.client().delete(
            collection_name=self.settings.collection_name,
            filter=knowledge_space_filter(scope),
        )
