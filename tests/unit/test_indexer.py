from ingestion.indexer import ingest_markdown_file
from ingestion.markdown_parser import DocumentScope
from sqlalchemy.orm.exc import StaleDataError


class FakeStore:
    def __init__(self):
        self.hashes = {"old-hash"}
        self.deleted = []
        self.added = []

    def indexed_hashes(self, scope, document_id):
        return self.hashes

    def delete_document(self, scope, document_id):
        self.deleted.append((scope, document_id))

    def add_documents(self, documents):
        self.added.extend(documents)


def test_changed_document_deletes_only_same_space(tmp_path):
    path = tmp_path / "guide.md"
    path.write_text("# Guide\n\nNew content.", encoding="utf-8")
    scope = DocumentScope("local-default", "space-a", "doc-a")
    store = FakeStore()

    inserted = ingest_markdown_file(path, scope, store=store)

    assert inserted > 0
    assert store.deleted == [(scope, "doc-a")]
    assert {doc.metadata["knowledge_space_id"] for doc in store.added} == {"space-a"}


class _CleanupStore:
    def __init__(self):
        self.deleted = []

    def delete_document(self, scope, document_id):
        self.deleted.append((scope, document_id))


def _make_job(markdown_path):
    class Job:
        id = "job-1"
        workspace_id = "local-default"
        knowledge_space_id = "space-a"
        document_id = "doc-a"
        status = "indexing"
        indexed_at = None
        error_message = None

    job = Job()
    job.markdown_path = str(markdown_path)
    return job


def test_index_job_commits_status_when_row_still_exists(tmp_path, monkeypatch):
    from ingestion.document_jobs import index_document_job

    markdown = tmp_path / "preview.md"
    markdown.write_text("# Guide\n\nBody.", encoding="utf-8")

    class OkSession:
        def __init__(self, job):
            self.job = job
            self.commit_count = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, model, job_id):
            return self.job if job_id == self.job.id else None

        def rollback(self):
            raise AssertionError("rollback should not happen on success")

        def commit(self):
            self.commit_count += 1

    job = _make_job(markdown)
    store = _CleanupStore()
    monkeypatch.setattr("ingestion.document_jobs.ingest_markdown_file", lambda path, scope: 1)

    index_document_job("job-1", lambda: OkSession(job), store=store)

    assert job.status == "indexed"
    assert job.indexed_at is not None
    assert job.error_message is None
    assert store.deleted == []


def test_index_job_cleans_orphan_vectors_when_row_deleted_during_indexing(tmp_path, monkeypatch):
    from ingestion.document_jobs import index_document_job

    markdown = tmp_path / "preview.md"
    markdown.write_text("# Guide\n\nBody.", encoding="utf-8")

    class StaleSession:
        def __init__(self, job):
            self.job = job
            self.rolled_back = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, model, job_id):
            return self.job if job_id == self.job.id else None

        def rollback(self):
            self.rolled_back = True

        def commit(self):
            raise StaleDataError(
                "UPDATE statement on table 'document_jobs' expected to update 1 row(s); 0 were matched."
            )

    store = _CleanupStore()
    session = StaleSession(_make_job(markdown))
    monkeypatch.setattr("ingestion.document_jobs.ingest_markdown_file", lambda path, scope: 1)

    index_document_job("job-1", lambda: session, store=store)

    assert session.rolled_back is True
    assert store.deleted == [(DocumentScope("local-default", "space-a", "doc-a"), "doc-a")]
