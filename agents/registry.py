from dataclasses import dataclass

from langchain_core.tools import BaseTool

from agents.tools import (
    add_persona_knowledge,
    delete_persona_document,
    delete_persona_memory,
    list_persona_documents,
    read_persona_memories,
    rename_persona,
    save_persona_memory,
    search_persona_knowledge,
    update_persona_memory,
    update_persona_profile,
    web_search,
)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    specialist: str
    tool: BaseTool
    requires_confirmation: bool = False
    mutates_data: bool = False


_TOOL_SPECS = (
    ToolSpec("search_persona_knowledge", "conversation", search_persona_knowledge),
    ToolSpec("web_search", "web", web_search),
    ToolSpec("list_persona_documents", "management", list_persona_documents),
    ToolSpec("read_persona_memories", "memory", read_persona_memories),
    ToolSpec("save_persona_memory", "memory", save_persona_memory, False, True),
    ToolSpec("update_persona_memory", "memory", update_persona_memory, False, True),
    ToolSpec("delete_persona_memory", "memory", delete_persona_memory, False, True),
    ToolSpec("add_persona_knowledge", "management", add_persona_knowledge, True, True),
    ToolSpec("rename_persona", "management", rename_persona, True, True),
    ToolSpec("update_persona_profile", "management", update_persona_profile, True, True),
    ToolSpec("delete_persona_document", "management", delete_persona_document, True, True),
)

READ_ONLY_TOOL_NAMES = tuple(spec.name for spec in _TOOL_SPECS if not spec.mutates_data)
AUTOMATIC_TOOL_NAMES = tuple(spec.name for spec in _TOOL_SPECS if not spec.requires_confirmation)
MUTATION_TOOL_NAMES = tuple(spec.name for spec in _TOOL_SPECS if spec.requires_confirmation)


def tool_specs() -> tuple[ToolSpec, ...]:
    return _TOOL_SPECS


def tools_for_specialist(specialist: str) -> list[BaseTool]:
    return [spec.tool for spec in _TOOL_SPECS if spec.specialist == specialist]


def specialist_for_tool(tool_name: str) -> str:
    return next((spec.specialist for spec in _TOOL_SPECS if spec.name == tool_name), "conversation")


def capability_summary() -> str:
    automatic = "、".join(AUTOMATIC_TOOL_NAMES)
    confirmed = "、".join(MUTATION_TOOL_NAMES)
    return f"可自动使用：{automatic}。需要你每次确认：{confirmed}。"
