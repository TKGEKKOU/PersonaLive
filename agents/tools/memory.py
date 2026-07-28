from langchain.tools import ToolRuntime, tool
from sqlalchemy import select

from agents.context import PersonaAgentContext
from app.models import PersonaMemory


def _session(context: PersonaAgentContext):
    if context.session_factory is None:
        raise RuntimeError("Database session is unavailable")
    return context.session_factory()


def memories_for_context(context: PersonaAgentContext) -> list[dict]:
    session = _session(context)
    try:
        statement = (
            select(PersonaMemory)
            .where(
                PersonaMemory.workspace_id == context.workspace_id,
                PersonaMemory.persona_id == context.persona_id,
            )
            .order_by(PersonaMemory.created_at, PersonaMemory.id)
        )
        return [
            {
                "id": memory.id,
                "content": memory.content,
                "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
            }
            for memory in session.scalars(statement)
        ]
    finally:
        session.close()


def save_memory_for_context(
    context: PersonaAgentContext,
    content: str,
) -> dict:
    content = content.strip()
    if not content:
        raise ValueError("memory content must not be empty")
    session = _session(context)
    try:
        memory = PersonaMemory(
            workspace_id=context.workspace_id,
            persona_id=context.persona_id,
            content=content,
        )
        session.add(memory)
        session.commit()
        session.refresh(memory)
        return {"status": "saved", "memory_id": memory.id, "content": memory.content}
    finally:
        session.close()


def update_memory_for_context(
    context: PersonaAgentContext,
    memory_id: str,
    content: str,
) -> dict:
    content = content.strip()
    if not content:
        raise ValueError("memory content must not be empty")
    session = _session(context)
    try:
        memory = session.scalar(
            select(PersonaMemory).where(
                PersonaMemory.id == memory_id,
                PersonaMemory.workspace_id == context.workspace_id,
                PersonaMemory.persona_id == context.persona_id,
            )
        )
        if memory is None:
            raise LookupError("Persona memory not found")
        memory.content = content
        session.commit()
        return {"status": "updated", "memory_id": memory.id, "content": memory.content}
    finally:
        session.close()


def delete_memory_for_context(
    context: PersonaAgentContext,
    memory_id: str,
) -> dict:
    session = _session(context)
    try:
        memory = session.scalar(
            select(PersonaMemory).where(
                PersonaMemory.id == memory_id,
                PersonaMemory.workspace_id == context.workspace_id,
                PersonaMemory.persona_id == context.persona_id,
            )
        )
        if memory is None:
            raise LookupError("Persona memory not found")
        session.delete(memory)
        session.commit()
        return {"status": "deleted", "memory_id": memory_id}
    finally:
        session.close()


@tool("read_persona_memories")
def read_persona_memories(runtime: ToolRuntime[PersonaAgentContext]) -> list[dict]:
    """Read long-term memories isolated to the active persona."""
    return memories_for_context(runtime.context)


@tool("save_persona_memory")
def save_persona_memory(content: str, runtime: ToolRuntime[PersonaAgentContext]) -> dict:
    """Save a user preference or durable fact for the active persona."""
    return save_memory_for_context(runtime.context, content)


@tool("update_persona_memory")
def update_persona_memory(
    memory_id: str,
    content: str,
    runtime: ToolRuntime[PersonaAgentContext],
) -> dict:
    """Update one long-term memory belonging to the active persona."""
    return update_memory_for_context(runtime.context, memory_id, content)


@tool("delete_persona_memory")
def delete_persona_memory(
    memory_id: str,
    runtime: ToolRuntime[PersonaAgentContext],
) -> dict:
    """Delete one long-term memory belonging to the active persona."""
    return delete_memory_for_context(runtime.context, memory_id)
