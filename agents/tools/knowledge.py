from typing import Any

from langchain.tools import ToolRuntime, tool

from agents.context import PersonaAgentContext
from agents.contracts import SpecialistResult
from rag.contracts import RagEvidenceResult, RagQueryContext
from rag.service import RagRequest, RagService, create_rag_service


"""Agent 侧 RAG 入口。

search_persona_knowledge 是知识 Worker 暴露给 supervisor 的唯一检索工具：
- 强制走知识链（force_knowledge=True），避免 RAG 内部再把同一问题误分派；
- 统一经 RagService 选择 adaptive 管线，返回的 RagEvidenceResult 只暴露
  通过质量门的证据与引用，未通过的草稿不会上抛给上层模型。
"""
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
    evidence_result = RagEvidenceResult.from_rag_result(result)
    return SpecialistResult.from_rag_evidence(evidence_result).as_dict()


@tool("search_persona_knowledge")
def search_persona_knowledge(
    query: str,
    runtime: ToolRuntime[PersonaAgentContext],
) -> dict[str, Any]:
    """Search the active persona's uploaded knowledge with corrective RAG."""
    return run_persona_knowledge_search(query, runtime.context)
