from sqlalchemy import func, select

from app.models import Persona
from persona.drafts import merge_candidates


def wait_for_draft(client, draft_id: str, tries: int = 50) -> dict:
    for _ in range(tries):
        draft = client.get(f"/api/persona-drafts/{draft_id}").json()
        if draft["status"] != "analyzing":
            return draft
    raise AssertionError("draft analysis did not finish")


def test_candidate_merge_combines_cross_chunk_fields_but_keeps_distinct_identities():
    merged = merge_candidates(
        [],
        [
            {"name": "林默", "profile": {"identity": "侦探", "personality": "冷静"}},
            {"name": "林默", "profile": {"identity": "医生", "voice": "温和"}},
        ],
    )
    merged = merge_candidates(
        merged,
        [{"name": "林默", "profile": {"identity": "侦探", "boundaries": "不伤害无辜"}}],
    )

    assert len(merged) == 2
    detective = next(item for item in merged if item["profile"]["identity"] == "侦探")
    assert detective["profile"]["personality"] == "冷静"
    assert detective["profile"]["boundaries"] == "不伤害无辜"


def test_batch_upload_creates_one_persona_only_after_confirm(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr("ingestion.document_jobs.DATA_DIR", tmp_path)

    def fake_convert(source, destination):
        text = f"# {source.stem}\n\nConverted content."
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")
        return text

    monkeypatch.setattr("ingestion.document_jobs.convert_source", fake_convert)
    monkeypatch.setattr(
        "app.routers.persona_drafts.analyze_materials",
        lambda mode, previews, fallback: ("旅行规划师", {"description": "提供旅行规划建议"}),
    )
    monkeypatch.setattr("ingestion.document_jobs.ingest_markdown_file", lambda path, scope: 1)

    uploaded = client.post(
        "/api/persona-drafts/upload",
        data={"mode": "expert"},
        files=[
            ("files", ("guide-a.txt", b"A", "text/plain")),
            ("files", ("guide-b.txt", b"B", "text/plain")),
        ],
    )

    assert uploaded.status_code == 201
    draft = uploaded.json()
    assert draft["mode"] == "expert"
    assert draft["status"] == "analyzing"
    draft = wait_for_draft(client, draft["id"])
    assert draft["status"] == "draft"
    assert draft["suggested_name"] == "旅行规划师"
    assert len(draft["documents"]) == 2
    assert db_session.scalar(select(func.count()).select_from(Persona)) == 0

    updated = client.patch(
        f"/api/persona-drafts/{draft['id']}",
        json={"name": "城市旅行专家", "profile": {"description": "熟悉城市旅行"}},
    )
    assert updated.status_code == 200
    assert updated.json()["suggested_name"] == "城市旅行专家"

    confirmed = client.post(f"/api/persona-drafts/{draft['id']}/confirm")
    assert confirmed.status_code == 200
    persona = confirmed.json()["persona"]
    assert persona["name"] == "城市旅行专家"
    assert db_session.scalar(select(func.count()).select_from(Persona)) == 1


    repeated = client.post(f"/api/persona-drafts/{draft['id']}/confirm")
    assert repeated.status_code == 200
    assert repeated.json()["persona"]["id"] == persona["id"]
    assert db_session.scalar(select(func.count()).select_from(Persona)) == 1


def test_character_draft_returns_all_candidates_and_requires_selection_before_confirm(
    client, db_session, tmp_path, monkeypatch
):
    monkeypatch.setattr("ingestion.document_jobs.DATA_DIR", tmp_path)
    monkeypatch.setattr(
        "ingestion.document_jobs.convert_source",
        lambda source, destination: "# Cast\n\nAlice is a detective.\nBob is a doctor.",
    )
    monkeypatch.setattr(
        "app.routers.persona_drafts.identify_candidates",
        lambda previews: [
            {"name": "Alice", "profile": {"description": "Detective"}},
            {"name": "Bob", "profile": {"description": "Doctor"}},
        ],
    )
    monkeypatch.setattr("ingestion.document_jobs.ingest_markdown_file", lambda path, scope: 1)

    uploaded = client.post(
        "/api/persona-drafts/upload",
        data={"mode": "character"},
        files=[("files", ("cast.txt", b"cast", "text/plain"))],
    )

    assert uploaded.status_code == 201
    draft = uploaded.json()
    assert draft["status"] == "analyzing"
    draft = wait_for_draft(client, draft["id"])
    assert draft["persona_type"] == "character"
    assert [candidate["name"] for candidate in draft["candidates"]] == ["Alice", "Bob"]
    assert draft["selected_candidate_id"] is None
    assert db_session.scalar(select(func.count()).select_from(Persona)) == 0

    rejected = client.post(f"/api/persona-drafts/{draft['id']}/confirm")
    assert rejected.status_code == 409
    assert db_session.scalar(select(func.count()).select_from(Persona)) == 0

    selected = client.post(
        f"/api/persona-drafts/{draft['id']}/candidates/{draft['candidates'][1]['id']}"
    )
    assert selected.status_code == 200
    assert selected.json()["selected_candidate_id"] == draft["candidates"][1]["id"]
    assert selected.json()["suggested_name"] == "Bob"

    confirmed = client.post(f"/api/persona-drafts/{draft['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["persona"]["persona_type"] == "character"


def test_character_draft_without_candidates_falls_back_to_knowledge_expert(client, tmp_path, monkeypatch):
    monkeypatch.setattr("ingestion.document_jobs.DATA_DIR", tmp_path)
    monkeypatch.setattr("ingestion.document_jobs.convert_source", lambda source, destination: "# Gardening")
    monkeypatch.setattr("app.routers.persona_drafts.identify_candidates", lambda previews: [])
    monkeypatch.setattr(
        "app.routers.persona_drafts.analyze_materials",
        lambda mode, previews, fallback: ("Gardening expert", {"description": "Answers about gardening"}),
    )

    uploaded = client.post(
        "/api/persona-drafts/upload",
        data={"mode": "character"},
        files=[("files", ("garden.txt", b"garden", "text/plain"))],
    )

    assert uploaded.status_code == 201
    draft = uploaded.json()
    assert draft["status"] == "analyzing"
    draft = wait_for_draft(client, draft["id"])
    assert draft["persona_type"] == "knowledge_expert"
    assert draft["candidates"] == []
    assert draft["selected_candidate_id"] is None
    assert client.post(f"/api/persona-drafts/{draft['id']}/confirm").status_code == 200


def test_expert_draft_skips_candidate_identification(client, tmp_path, monkeypatch):
    monkeypatch.setattr("ingestion.document_jobs.DATA_DIR", tmp_path)
    monkeypatch.setattr("ingestion.document_jobs.convert_source", lambda source, destination: "# Physics")
    monkeypatch.setattr(
        "app.routers.persona_drafts.identify_candidates",
        lambda previews: (_ for _ in ()).throw(AssertionError("expert mode must bypass candidates")),
    )
    monkeypatch.setattr(
        "app.routers.persona_drafts.analyze_materials",
        lambda mode, previews, fallback: ("Physics expert", {"description": "Explains physics"}),
    )

    uploaded = client.post(
        "/api/persona-drafts/upload",
        data={"mode": "expert"},
        files=[("files", ("physics.txt", b"physics", "text/plain"))],
    )

    assert uploaded.status_code == 201
    draft = wait_for_draft(client, uploaded.json()["id"])
    assert draft["persona_type"] == "knowledge_expert"
    assert draft["candidates"] == []
