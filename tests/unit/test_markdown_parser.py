from ingestion.markdown_parser import DocumentScope, MarkdownParser


def test_parser_uses_content_hash_and_keeps_scopes_isolated(tmp_path):
    content = "# Guide\n\nScoped content."
    first_path = tmp_path / "first.md"
    second_path = tmp_path / "second.md"
    first_path.write_text(content, encoding="utf-8")
    second_path.write_text(content, encoding="utf-8")

    parser = MarkdownParser()
    first_documents = parser.parse_file(
        first_path,
        DocumentScope("local-default", "space-a", "doc-a"),
    )
    second_documents = parser.parse_file(
        second_path,
        DocumentScope("local-default", "space-b", "doc-b"),
    )

    assert first_documents
    assert {document.metadata["source_hash"] for document in first_documents} == {
        document.metadata["source_hash"] for document in second_documents
    }
    assert {
        (document.metadata["workspace_id"], document.metadata["knowledge_space_id"], document.metadata["document_id"])
        for document in first_documents
    } == {("local-default", "space-a", "doc-a")}
    assert {
        (document.metadata["workspace_id"], document.metadata["knowledge_space_id"], document.metadata["document_id"])
        for document in second_documents
    } == {("local-default", "space-b", "doc-b")}
