from dataclasses import replace

import pytest

from ingestion.milvus_store import MilvusRagStore
from settings import Settings


class FakeMilvusClient:
    def list_collections(self):
        return ["personalive_knowledge_v1"]

    def describe_collection(self, collection_name):
        return {"fields": [{"name": "dense", "params": {"dim": 512}}]}


def test_existing_collection_rejects_different_embedding_dimensions(tmp_path):
    settings = replace(
        Settings.load(tmp_path),
        embedding_dimensions=1024,
        collection_name="personalive_knowledge_v1",
    )
    store = MilvusRagStore(settings)

    with pytest.raises(RuntimeError, match="Milvus Collection 为 512 维"):
        store.validate_collection_dimensions(FakeMilvusClient())
