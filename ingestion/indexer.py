from pathlib import Path

from ingestion.markdown_parser import DocumentScope, MarkdownParser
from ingestion.milvus_store import MilvusRagStore


def ingest_markdown_file(
    path: Path,
    scope: DocumentScope,
    store: MilvusRagStore | None = None,
) -> int:
    if not path.is_file() or path.suffix.lower() != ".md":
        raise FileNotFoundError(f"Markdown file does not exist: {path}")

    active_store = store or MilvusRagStore()
    if store is None:
        active_store.create_collection(reset=False)

    documents = MarkdownParser().parse_file(path, scope)
    if not documents:
        return 0

    source_hash = str(documents[0].metadata["source_hash"])
    old_hashes = active_store.indexed_hashes(scope, scope.document_id)
    if source_hash in old_hashes:
        return 0
    if old_hashes:
        active_store.delete_document(scope, scope.document_id)

    active_store.add_documents(documents)
    return len(documents)
