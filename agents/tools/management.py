from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain.tools import ToolRuntime, tool
from langgraph.types import interrupt
from sqlalchemy import select

from agents.context import PersonaAgentContext
from app.models import DocumentJob, Persona
from ingestion.document_jobs import DATA_DIR, index_document_job, sanitize_filename
from ingestion.markdown_parser import DocumentScope
from ingestion.milvus_store import MilvusRagStore


Confirmer = Callable[[dict[str, Any]], bool]
Indexer = Callable[[str, Any], None]


# Human-in-the-loop 确认器：所有变更类管理工具默认注入 request_confirmation，
# 通过 LangGraph interrupt() 暂停图执行并把 action（工具名/标题/参数）抛给 API/UI；
# 用户批准或拒绝后由 service.resume 以 Command(resume=...) 恢复。测试可用假确认器
# 注入，避免真正触发中断。
def request_confirmation(action: dict[str, Any]) -> bool:
    """暂停 LangGraph，将待执行写操作交给 API/UI 确认后再从检查点恢复。"""

    decision = interrupt(action)
    if isinstance(decision, dict):
        return bool(decision.get("approved", False))
    return bool(decision)


def _session(context: PersonaAgentContext):
    if context.session_factory is None:
        raise RuntimeError("Database session is unavailable")
    return context.session_factory()


def list_documents_for_context(context: PersonaAgentContext) -> list[dict]:
    if context.session_factory is None:
        return []
    session = _session(context)
    try:
        # workspace + knowledge_space 双重过滤是权限边界，不能依赖模型传入作用域。
        statement = (
            select(DocumentJob)
            .where(
                DocumentJob.workspace_id == context.workspace_id,
                DocumentJob.knowledge_space_id.in_(context.knowledge_space_ids),
                DocumentJob.status != "deleted",
            )
            .order_by(DocumentJob.created_at, DocumentJob.id)
        )
        return [
            {
                "id": document.id,
                "filename": document.original_filename,
                "status": document.status,
                "indexed_at": document.indexed_at.isoformat() if document.indexed_at else None,
            }
            for document in session.scalars(statement)
        ]
    finally:
        session.close()


def rename_persona_for_context(
    context: PersonaAgentContext,
    name: str,
    confirmer: Confirmer = request_confirmation,
) -> dict:
    name = name.strip()
    if not name:
        raise ValueError("persona name must not be empty")
    action = {
        "tool": "rename_persona",
        "title": "重命名角色",
        "target": context.persona_name,
        "arguments": {"name": name},
    }
    # 先确认、后开启写会话，避免暂停期间持有数据库事务或锁。
    if not confirmer(action):
        return {"status": "cancelled"}
    session = _session(context)
    try:
        persona = session.scalar(
            select(Persona).where(
                Persona.id == context.persona_id,
                Persona.workspace_id == context.workspace_id,
            )
        )
        if persona is None:
            raise LookupError("Persona not found")
        persona.name = name
        session.commit()
        return {"status": "renamed", "persona_id": persona.id, "name": name}
    finally:
        session.close()


def update_profile_for_context(
    context: PersonaAgentContext,
    profile: dict[str, Any],
    confirmer: Confirmer = request_confirmation,
) -> dict:
    action = {
        "tool": "update_persona_profile",
        "title": "修改角色设定",
        "target": context.persona_name,
        "arguments": {"profile": profile},
    }
    if not confirmer(action):
        return {"status": "cancelled"}
    session = _session(context)
    try:
        persona = session.scalar(
            select(Persona).where(
                Persona.id == context.persona_id,
                Persona.workspace_id == context.workspace_id,
            )
        )
        if persona is None:
            raise LookupError("Persona not found")
        persona.profile_json = {**(persona.profile_json or {}), **profile}
        session.commit()
        return {"status": "updated", "persona_id": persona.id, "profile": persona.profile_json}
    finally:
        session.close()


def add_knowledge_for_context(
    context: PersonaAgentContext,
    content: str,
    title: str = "对话补充资料",
    confirmer: Confirmer = request_confirmation,
    indexer: Indexer = index_document_job,
    data_dir: Path | None = None,
) -> dict:
    content = content.strip()
    title = title.strip() or "对话补充资料"
    if not content:
        raise ValueError("knowledge content must not be empty")
    action = {
        "tool": "add_persona_knowledge",
        "title": "追加角色知识",
        "target": context.persona_name,
        "arguments": {"title": title, "content": content},
    }
    if not confirmer(action):
        return {"status": "cancelled"}

    session = _session(context)
    try:
        persona = session.scalar(
            select(Persona).where(
                Persona.id == context.persona_id,
                Persona.workspace_id == context.workspace_id,
                Persona.knowledge_space_id.in_(context.knowledge_space_ids),
            )
        )
        if persona is None:
            raise LookupError("Persona not found")

        job_id = str(uuid4())
        document_id = str(uuid4())
        job_dir = (data_dir or DATA_DIR) / "staging" / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        safe_title = sanitize_filename(title)
        filename = safe_title if Path(safe_title).suffix.lower() == ".md" else f"{safe_title}.md"
        source_path = job_dir / filename
        markdown_path = job_dir / "preview.md"
        source_path.write_text(content, encoding="utf-8")
        markdown_path.write_text(content, encoding="utf-8")

        job = DocumentJob(
            id=job_id,
            workspace_id=context.workspace_id,
            knowledge_space_id=persona.knowledge_space_id,
            document_id=document_id,
            original_filename=filename,
            markdown_filename=filename,
            source_path=str(source_path),
            markdown_path=str(markdown_path),
            markdown_preview=content,
            status="indexing",
        )
        session.add(job)
        session.commit()
    finally:
        session.close()

    indexer(job_id, context.session_factory)
    result_session = _session(context)
    try:
        result = result_session.get(DocumentJob, job_id)
        return {
            "status": result.status if result else "indexing",
            "job_id": job_id,
            "document_id": document_id,
            "filename": filename,
        }
    finally:
        result_session.close()


def delete_document_for_context(
    context: PersonaAgentContext,
    document_id: str,
    confirmer: Confirmer = request_confirmation,
    store: MilvusRagStore | None = None,
) -> dict:
    action = {
        "tool": "delete_persona_document",
        "title": "删除角色资料",
        "target": document_id,
        "arguments": {},
    }
    if not confirmer(action):
        return {"status": "cancelled"}
    session = _session(context)
    try:
        document = session.scalar(
            select(DocumentJob).where(
                DocumentJob.id == document_id,
                DocumentJob.workspace_id == context.workspace_id,
                DocumentJob.knowledge_space_id.in_(context.knowledge_space_ids),
            )
        )
        if document is None:
            raise LookupError("Persona document not found")
        active_store = store or MilvusRagStore()
        active_store.delete_document(
            DocumentScope(
                workspace_id=context.workspace_id,
                knowledge_space_id=document.knowledge_space_id,
                document_id=document.document_id,
            ),
            document.document_id,
        )
        document.status = "deleted"
        session.commit()
        return {"status": "deleted", "document_id": document.id}
    finally:
        session.close()


@tool("list_persona_documents")
def list_persona_documents(runtime: ToolRuntime[PersonaAgentContext]) -> list[dict]:
    """List documents belonging to the active persona only."""
    return list_documents_for_context(runtime.context)


@tool("rename_persona")
def rename_persona(name: str, runtime: ToolRuntime[PersonaAgentContext]) -> dict:
    """Rename the active persona."""
    return rename_persona_for_context(runtime.context, name)


@tool("update_persona_profile")
def update_persona_profile(
    profile: dict[str, Any],
    runtime: ToolRuntime[PersonaAgentContext],
) -> dict:
    """Merge updates into the active persona profile."""
    return update_profile_for_context(runtime.context, profile)


@tool("add_persona_knowledge")
def add_persona_knowledge(
    content: str,
    runtime: ToolRuntime[PersonaAgentContext],
    title: str = "对话补充资料",
) -> dict:
    """Append user-supplied factual knowledge to the active persona after confirmation."""
    return add_knowledge_for_context(runtime.context, content, title)


@tool("delete_persona_document")
def delete_persona_document(
    document_id: str,
    runtime: ToolRuntime[PersonaAgentContext],
) -> dict:
    """Delete one uploaded document from the active persona knowledge space."""
    return delete_document_for_context(runtime.context, document_id)
