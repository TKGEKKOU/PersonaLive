"""应用内置 Embedding 的资源管理与推理适配。"""

from ingestion.local_embedding.client import ManagedLocalEmbeddings
from ingestion.local_embedding.resources import LocalEmbeddingResourceManager

__all__ = ["LocalEmbeddingResourceManager", "ManagedLocalEmbeddings"]
