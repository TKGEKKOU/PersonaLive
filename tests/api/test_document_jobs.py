def _create_persona(client):
    response = client.post("/api/personas", json={"name": "Alpha"})
    assert response.status_code == 201
    return response.json()


def test_multi_file_upload_and_confirm_use_server_scope(client, tmp_path, monkeypatch):
    from ingestion.markdown_parser import DocumentScope

    persona = _create_persona(client)
    monkeypatch.setattr("ingestion.document_jobs.DATA_DIR", tmp_path)

    def fake_convert(source, destination):
        text = f"# {source.stem}\n\nConverted content."
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        return text

    captured = {}

    def fake_ingest(path, scope):
        captured["path"] = path
        captured["scope"] = scope
        return 1

    monkeypatch.setattr("ingestion.document_jobs.convert_source", fake_convert)
    monkeypatch.setattr("ingestion.document_jobs.ingest_markdown_file", fake_ingest)

    response = client.post(
        f"/api/knowledge-spaces/{persona['knowledge_space_id']}/documents/upload",
        files=[
            ("files", ("a.html", b"<h1>A</h1>", "text/html")),
            ("files", ("b.html", b"<h1>B</h1>", "text/html")),
        ],
    )

    assert response.status_code == 201
    jobs = response.json()
    assert [job["original_filename"] for job in jobs] == ["a.html", "b.html"]
    assert [job["status"] for job in jobs] == ["preview_ready", "preview_ready"]
    assert {job["knowledge_space_id"] for job in jobs} == {persona["knowledge_space_id"]}

    confirmed = client.post(f"/api/documents/{jobs[0]['id']}/confirm")
    assert confirmed.status_code == 200
    assert captured["scope"] == DocumentScope(
        "local-default", persona["knowledge_space_id"], jobs[0]["document_id"]
    )
    assert client.get(f"/api/documents/{jobs[0]['id']}").json()["status"] == "indexed"


def test_upload_rejects_unknown_knowledge_space(client):
    response = client.post(
        "/api/knowledge-spaces/missing/documents/upload",
        files=[("files", ("a.txt", b"A", "text/plain"))],
    )

    assert response.status_code == 404


def test_delete_document_removes_job_and_vectors(client, tmp_path, monkeypatch):
    from ingestion.markdown_parser import DocumentScope

    persona = _create_persona(client)
    monkeypatch.setattr("ingestion.document_jobs.DATA_DIR", tmp_path)
    monkeypatch.setattr(
        "ingestion.document_jobs.convert_source",
        lambda source, destination: "# Delete me",
    )
    monkeypatch.setattr("ingestion.document_jobs.ingest_markdown_file", lambda path, scope: 1)

    uploaded = client.post(
        f"/api/knowledge-spaces/{persona['knowledge_space_id']}/documents/upload",
        files=[("files", ("a.txt", b"A", "text/plain"))],
    )
    job = uploaded.json()[0]
    client.post(f"/api/documents/{job['id']}/confirm")

    deleted_vectors = []
    class FakeStore:
        def __init__(self):
            pass
        def delete_document(self, scope, document_id):
            deleted_vectors.append((scope, document_id))

    monkeypatch.setattr("app.routers.documents.MilvusRagStore", FakeStore)
    response = client.delete(f"/api/documents/{job['id']}")
    assert response.status_code == 204
    assert client.get(f"/api/documents/{job['id']}").status_code == 404
    assert deleted_vectors == [
        (DocumentScope("local-default", persona["knowledge_space_id"], job["document_id"]), job["document_id"])
    ]


def test_delete_indexing_job_cleans_vectors_too(client, db_session, tmp_path, monkeypatch):
    from uuid import uuid4

    from app.models import DocumentJob
    from ingestion.markdown_parser import DocumentScope

    persona = _create_persona(client)
    job_id = str(uuid4())
    document_id = str(uuid4())
    db_session.add(
        DocumentJob(
            id=job_id,
            workspace_id="local-default",
            knowledge_space_id=persona["knowledge_space_id"],
            document_id=document_id,
            original_filename="pending.md",
            markdown_filename="pending.md",
            source_path=str(tmp_path / "pending.md"),
            markdown_path=str(tmp_path / "preview.md"),
            status="indexing",
        )
    )
    db_session.commit()

    deleted = []

    class FakeStore:
        def delete_document(self, scope, document_id):
            deleted.append((scope, document_id))

    monkeypatch.setattr("app.routers.documents.MilvusRagStore", FakeStore)
    response = client.delete(f"/api/documents/{job_id}")

    assert response.status_code == 204
    assert deleted == [
        (DocumentScope("local-default", persona["knowledge_space_id"], document_id), document_id)
    ]
