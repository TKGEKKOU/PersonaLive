from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import KnowledgeSpace, Persona

LOCAL_WORKSPACE_ID = "local-default"


@dataclass(frozen=True)
class KnowledgeScope:
    workspace_id: str
    knowledge_space_ids: tuple[str, ...]


class PersonaNotFound(LookupError):
    pass


def create_persona(
    session: Session, name: str, profile: dict | None = None
) -> Persona:
    knowledge_space = KnowledgeSpace(workspace_id=LOCAL_WORKSPACE_ID)
    session.add(knowledge_space)
    session.flush()

    persona = Persona(
        workspace_id=LOCAL_WORKSPACE_ID,
        knowledge_space_id=knowledge_space.id,
        name=name,
        profile_json=profile or {},
        status="ready",
    )
    session.add(persona)
    session.flush()
    return persona


def resolve_knowledge_scope(session: Session, persona_id: str) -> KnowledgeScope:
    persona = session.get(Persona, persona_id)
    if persona is None or persona.workspace_id != LOCAL_WORKSPACE_ID:
        raise PersonaNotFound(persona_id)
    knowledge_space = session.get(KnowledgeSpace, persona.knowledge_space_id)
    if knowledge_space is None or knowledge_space.workspace_id != LOCAL_WORKSPACE_ID:
        raise PersonaNotFound(persona_id)
    return KnowledgeScope(LOCAL_WORKSPACE_ID, (persona.knowledge_space_id,))


def find_persona_by_name(session: Session, name: str) -> Persona | None:
    statement = (
        select(Persona)
        .where(Persona.workspace_id == LOCAL_WORKSPACE_ID, Persona.name == name)
        .order_by(Persona.created_at, Persona.id)
    )
    return session.scalars(statement).first()
