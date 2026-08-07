import pytest

from app.models import KnowledgeSpace, Persona


def test_each_persona_resolves_only_its_own_knowledge_space(db_session):
    from persona.service import create_persona, resolve_knowledge_scope

    alpha = create_persona(db_session, "Alpha")
    beta = create_persona(db_session, "Beta")

    alpha_scope = resolve_knowledge_scope(db_session, alpha.id)
    beta_scope = resolve_knowledge_scope(db_session, beta.id)

    assert alpha_scope.workspace_id == "local-default"
    assert alpha_scope.knowledge_space_ids == (alpha.knowledge_space_id,)
    assert beta_scope.knowledge_space_ids == (beta.knowledge_space_id,)
    assert alpha_scope.knowledge_space_ids != beta_scope.knowledge_space_ids


def test_scope_rejects_local_persona_linked_to_foreign_knowledge_space(db_session):
    from persona.service import PersonaNotFound, resolve_knowledge_scope

    foreign_space = KnowledgeSpace(workspace_id="other-workspace")
    db_session.add(foreign_space)
    db_session.flush()
    persona = Persona(
        workspace_id="local-default",
        knowledge_space_id=foreign_space.id,
        name="Invalid link",
    )
    db_session.add(persona)
    db_session.flush()

    with pytest.raises(PersonaNotFound):
        resolve_knowledge_scope(db_session, persona.id)
