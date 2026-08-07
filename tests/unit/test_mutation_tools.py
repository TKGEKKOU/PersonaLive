import pytest
from sqlalchemy import func, select

from agents.context import PersonaAgentContext
from agents.registry import AUTOMATIC_TOOL_NAMES, MUTATION_TOOL_NAMES, tool_specs
from agents.tools.memory import save_memory_for_context, update_memory_for_context
from agents.tools.management import add_knowledge_for_context, rename_persona_for_context
from app.models import DocumentJob, PersonaMemory
from persona.service import create_persona


def context_for(persona, db_session):
    return PersonaAgentContext(
        persona_id=persona.id,
        workspace_id="local-default",
        knowledge_space_ids=(persona.knowledge_space_id,),
        conversation_id="thread-a",
        persona_name=persona.name,
        persona_type=persona.persona_type,
        persona_profile=persona.profile_json,
        session_factory=lambda: db_session,
    )


def test_memory_write_runs_without_confirmation(db_session):
    persona = create_persona(db_session, "Alpha")
    db_session.commit()

    result = save_memory_for_context(context_for(persona, db_session), "用户喜欢红茶")

    assert result["status"] == "saved"
    assert db_session.scalar(select(func.count()).select_from(PersonaMemory)) == 1


def test_confirmed_memory_is_scoped_and_cross_persona_update_is_rejected(db_session):
    first = create_persona(db_session, "First")
    second = create_persona(db_session, "Second")
    db_session.commit()
    created = save_memory_for_context(
        context_for(first, db_session),
        "用户喜欢红茶",
    )

    memory = db_session.get(PersonaMemory, created["memory_id"])
    assert memory.persona_id == first.id
    with pytest.raises(LookupError):
        update_memory_for_context(
            context_for(second, db_session),
            memory.id,
            "用户喜欢咖啡",
        )


def test_each_persona_rename_requests_confirmation(db_session):
    persona = create_persona(db_session, "Alpha")
    db_session.commit()
    confirmations = []
    context = context_for(persona, db_session)

    rename_persona_for_context(context, "Beta", confirmer=lambda action: confirmations.append(action) or True)
    rename_persona_for_context(context, "Gamma", confirmer=lambda action: confirmations.append(action) or True)

    assert len(confirmations) == 2
    assert db_session.get(type(persona), persona.id).name == "Gamma"


def test_add_persona_knowledge_is_confirmed_and_scoped(db_session, tmp_path):
    persona = create_persona(db_session, "Alpha")
    db_session.commit()
    confirmations = []
    indexed = []

    result = add_knowledge_for_context(
        context_for(persona, db_session),
        "# 共鸣回路\n\n光学取样。",
        title="共鸣回路",
        confirmer=lambda action: confirmations.append(action) or True,
        indexer=lambda job_id, session_factory: indexed.append(job_id),
        data_dir=tmp_path,
    )

    job = db_session.get(DocumentJob, result["job_id"])
    assert confirmations[0]["tool"] == "add_persona_knowledge"
    assert job.knowledge_space_id == persona.knowledge_space_id
    assert job.markdown_preview == "# 共鸣回路\n\n光学取样。"
    assert indexed == [job.id]


def test_registry_marks_every_mutation_as_confirmed():
    expected = {
        "add_persona_knowledge",
        "rename_persona",
        "update_persona_profile",
        "delete_persona_document",
    }
    assert set(MUTATION_TOOL_NAMES) == expected
    mutation_specs = [spec for spec in tool_specs() if spec.name in expected]
    assert mutation_specs and all(spec.requires_confirmation for spec in mutation_specs)
    assert {
        "save_persona_memory",
        "update_persona_memory",
        "delete_persona_memory",
    }.issubset(AUTOMATIC_TOOL_NAMES)
