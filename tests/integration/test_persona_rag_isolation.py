import os
from uuid import uuid4

import pytest
from langchain_core.documents import Document

from ingestion.markdown_parser import DocumentScope
from ingestion.milvus_store import MilvusRagStore
from rag.contracts import RagQueryContext
from rag.retriever import build_retriever


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_MILVUS_INTEGRATION") != "1",
    reason="set RUN_MILVUS_INTEGRATION=1 to test real Milvus isolation",
)
def test_persona_retrieval_never_crosses_knowledge_spaces():
    suffix = uuid4().hex
    store = MilvusRagStore()
    store.create_collection(reset=False)
    scopes = [
        DocumentScope("local-default", f"space-a-{suffix}", f"doc-a-{suffix}"),
        DocumentScope("local-default", f"space-b-{suffix}", f"doc-b-{suffix}"),
    ]
    documents = []
    for index, scope in enumerate(scopes):
        documents.append(
            Document(
                page_content=f"Persona isolation fact {index} {suffix}",
                metadata={
                    "source": "integration",
                    "filename": f"isolation-{index}.md",
                    "filetype": "text/markdown",
                    "title": "Isolation",
                    "section": "Isolation",
                    "category": "content",
                    "doc_id": scope.document_id,
                    "chunk_id": f"{scope.document_id}:0000",
                    "source_hash": suffix,
                    "workspace_id": scope.workspace_id,
                    "knowledge_space_id": scope.knowledge_space_id,
                    "document_id": scope.document_id,
                },
            )
        )
    store.add_documents(documents)
    try:
        for index, scope in enumerate(scopes):
            context = RagQueryContext(
                f"persona-{index}", scope.workspace_id, (scope.knowledge_space_id,)
            )
            results = build_retriever(context, k=2).invoke(
                f"Persona isolation fact {index} {suffix}"
            )
            assert results
            assert {item.metadata["knowledge_space_id"] for item in results} == {
                scope.knowledge_space_id
            }
    finally:
        for scope in scopes:
            store.delete_document(scope, scope.document_id)
