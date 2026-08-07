from app.models import (
    ConversationMessage,
    DocumentJob,
    KnowledgeSpace,
    Persona,
    PersonaDraft,
    PersonaMemory,
)


def test_create_persona_derives_server_owned_scope(client):
    response = client.post(
        "/api/personas",
        json={"name": "Alpha", "profile": {"style": "calm"}},
    )

    assert response.status_code == 201
    persona = response.json()
    assert persona["workspace_id"] == "local-default"
    assert persona["knowledge_space_id"]
    assert persona["profile"] == {"style": "calm"}
    assert persona["status"] == "ready"


def test_create_persona_rejects_client_owned_scope_fields(client):
    response = client.post(
        "/api/personas",
        json={
            "name": "Alpha",
            "workspace_id": "another-workspace",
            "knowledge_space_id": "another-space",
        },
    )

    assert response.status_code == 422


def test_list_personas_returns_only_local_workspace_records(client, db_session):
    client.post("/api/personas", json={"name": "Alpha"})
    foreign_space = KnowledgeSpace(workspace_id="other-workspace")
    db_session.add(foreign_space)
    db_session.flush()
    db_session.add(
        Persona(
            workspace_id="other-workspace",
            knowledge_space_id=foreign_space.id,
            name="Hidden",
        )
    )
    db_session.commit()
    response = client.get("/api/personas")

    assert response.status_code == 200
    assert [persona["name"] for persona in response.json()] == ["Alpha"]


def test_get_and_update_persona_profile(client):
    persona = client.post(
        "/api/personas", json={"name": "Alpha", "profile": {"voice": "calm"}}
    ).json()

    updated = client.patch(
        f"/api/personas/{persona['id']}",
        json={"name": "Beta", "profile": {"description": "updated"}},
    )

    assert updated.status_code == 200
    assert updated.json()["name"] == "Beta"
    assert updated.json()["profile"] == {"voice": "calm", "description": "updated"}
    assert client.get(f"/api/personas/{persona['id']}").json()["name"] == "Beta"


def test_list_persona_documents_is_scoped(client, db_session):
    persona = client.post("/api/personas", json={"name": "Alpha"}).json()
    db_session.add(
        DocumentJob(
            workspace_id="local-default",
            knowledge_space_id=persona["knowledge_space_id"],
            original_filename="profile.md",
            markdown_filename="profile.md",
            source_path="profile.md",
            status="indexed",
        )
    )
    db_session.commit()

    response = client.get(f"/api/personas/{persona['id']}/documents")

    assert response.status_code == 200
    assert [item["original_filename"] for item in response.json()] == ["profile.md"]


def test_delete_persona_removes_only_its_owned_data(db_session, tmp_path):
    from persona.delete_service import PersonaDeletionService

    first_space = KnowledgeSpace(workspace_id="local-default")
    second_space = KnowledgeSpace(workspace_id="local-default")
    db_session.add_all([first_space, second_space])
    db_session.flush()
    first = Persona(workspace_id="local-default", knowledge_space_id=first_space.id, name="Alpha")
    second = Persona(workspace_id="local-default", knowledge_space_id=second_space.id, name="Beta")
    db_session.add_all([first, second])
    db_session.flush()
    job_dir = tmp_path / "staging" / "job-a"
    job_dir.mkdir(parents=True)
    source = job_dir / "profile.md"
    source.write_text("profile", encoding="utf-8")
    db_session.add_all(
        [
            PersonaMemory(workspace_id="local-default", persona_id=first.id, content="memory"),
            PersonaDraft(
                workspace_id="local-default",
                knowledge_space_id=first_space.id,
                persona_id=first.id,
                mode="character",
                suggested_name="Alpha",
                status="confirmed",
            ),
            DocumentJob(
                id="job-a",
                workspace_id="local-default",
                knowledge_space_id=first_space.id,
                original_filename="profile.md",
                markdown_filename="profile.md",
                source_path=str(source),
                status="indexed",
            ),
            ConversationMessage(
                workspace_id="local-default",
                persona_id=first.id,
                conversation_id="conv-1",
                role="user",
                kind="text",
                content="你好",
            ),
        ]
    )
    db_session.commit()

    deleted_scopes = []
    deleted_checkpoints = []

    class FakeVectorStore:
        def delete_knowledge_space(self, scope):
            deleted_scopes.append(scope)

    service = PersonaDeletionService(
        vector_store=FakeVectorStore(),
        checkpoint_cleaner=deleted_checkpoints.append,
        data_dir=tmp_path,
    )
    service.delete(db_session, first.id)

    assert db_session.get(Persona, first.id) is None
    assert db_session.get(KnowledgeSpace, first_space.id) is None
    assert db_session.get(Persona, second.id) is not None
    assert db_session.query(PersonaMemory).filter_by(persona_id=first.id).count() == 0
    assert db_session.query(ConversationMessage).filter_by(persona_id=first.id).count() == 0
    assert deleted_scopes[0].knowledge_space_id == first_space.id
    assert deleted_checkpoints == [first.id]
    assert not job_dir.exists()


def test_delete_persona_succeeds_when_milvus_unavailable(db_session, tmp_path):
    from persona.delete_service import PersonaDeletionService

    space = KnowledgeSpace(workspace_id="local-default")
    db_session.add(space)
    db_session.flush()
    persona = Persona(workspace_id="local-default", knowledge_space_id=space.id, name="Alpha")
    db_session.add(persona)
    db_session.flush()
    db_session.add(
        ConversationMessage(
            workspace_id="local-default",
            persona_id=persona.id,
            conversation_id="conv-1",
            role="user",
            kind="text",
            content="你好",
        )
    )
    db_session.commit()

    class FailingVectorStore:
        def delete_knowledge_space(self, scope):
            raise ConnectionError("Milvus is down")

    service = PersonaDeletionService(
        vector_store=FailingVectorStore(),
        checkpoint_cleaner=lambda persona_id: None,
        data_dir=tmp_path,
    )
    service.delete(db_session, persona.id)

    assert db_session.get(Persona, persona.id) is None
    assert db_session.get(KnowledgeSpace, space.id) is None
    assert db_session.query(ConversationMessage).filter_by(persona_id=persona.id).count() == 0


def test_delete_missing_persona_returns_404(client):
    response = client.delete("/api/personas/missing")

    assert response.status_code == 404
