from typing import Any

from langchain.tools import ToolRuntime, tool

from agents.context import PersonaAgentContext
from rag.contracts import RagQueryContext
from rag.service import RagRequest, RagService, create_rag_service


def run_persona_knowledge_search(
    query: str,
    context: PersonaAgentContext,
    service: RagService | None = None,
) -> dict[str, Any]:
    active_service = service or create_rag_service()
    result = active_service.query(
        RagRequest(
            question=query,
            context=RagQueryContext(
                persona_id=context.persona_id,
                workspace_id=context.workspace_id,
                knowledge_space_ids=context.knowledge_space_ids,
                conversation_id=context.conversation_id,
            ),
            allow_web_fallback=False,
            persona_name=context.persona_name,
            persona_profile=context.persona_profile,
            force_knowledge=True,
        )
    )
    return {
        "answer": result.answer_draft,
        "evidence": list(result.evidence),
        "confidence": result.confidence,
        "missing_points": list(result.missing_points),
        "trace": list(result.trace),
    }


@tool("search_persona_knowledge")
def search_persona_knowledge(
    query: str,
    runtime: ToolRuntime[PersonaAgentContext],
) -> dict[str, Any]:
    """Search the active persona's uploaded knowledge with corrective RAG."""
    return run_persona_knowledge_search(query, runtime.context)

