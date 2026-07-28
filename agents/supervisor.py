import json
from collections.abc import Callable
from typing import Literal

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langgraph.constants import END, START
from langgraph.graph import MessagesState, StateGraph
from langgraph.runtime import Runtime

from agents.context import PersonaAgentContext
from agents.registry import tools_for_specialist
from rag.llm import get_llm


Specialist = Literal["conversation", "web", "memory", "management"]
WEB_SIGNALS = ("联网", "新闻", "天气", "实时", "最新", "搜索网络", "web")
MEMORY_SIGNALS = ("记住", "记忆", "忘记", "我的偏好", "我喜欢", "我不喜欢")
MANAGEMENT_SIGNALS = (
    "重命名",
    "改名",
    "修改角色",
    "删除资料",
    "文档列表",
    "列出资料",
    "有哪些资料",
    "角色的资料",
)

MANAGEMENT_MUTATION_SIGNALS = (
    "add_persona_knowledge",
    "rename_persona",
    "update_persona_profile",
    "delete_persona_document",
    "名字改为",
    "名字改成",
    "修改名字",
    "增加设定",
    "新增设定",
    "加上一些设定",
    "更新设定",
    "修改设定",
    "更新人设",
    "修改人设",
)

PERSONA_KNOWLEDGE_SIGNALS = (
    "这是你的",
    "加入你的资料",
    "追加你的资料",
    "补充你的资料",
    "加入你的设定",
    "补充你的设定",
)


class SupervisorState(MessagesState):
    specialist: Specialist


def route_specialist(question: str) -> Specialist:
    normalized = question.strip().lower()
    if any(signal in normalized for signal in WEB_SIGNALS):
        return "web"
    if any(signal in normalized for signal in PERSONA_KNOWLEDGE_SIGNALS):
        return "management"
    if any(signal in normalized for signal in MEMORY_SIGNALS):
        return "memory"
    if any(signal in normalized for signal in MANAGEMENT_SIGNALS + MANAGEMENT_MUTATION_SIGNALS):
        return "management"
    return "conversation"


def specialist_prompt(specialist: Specialist, context: PersonaAgentContext) -> str:
    profile = json.dumps(context.persona_profile, ensure_ascii=False)
    identity_rule = (
        "始终严格遵循人物设定，不得擅自改变身份、语气或行为边界。"
        if context.persona_type == "character"
        else "你是资料领域专家，不虚构人物经历；资料不足时明确说明。"
    )
    duties = {
        "conversation": "负责自然对话；涉及上传资料事实或人物经历时调用知识检索工具。",
        "web": "负责获取实时公开信息，并清楚区分联网结果与角色资料。",
        "memory": "负责读取和维护当前角色隔离的用户长期记忆；明确的长期偏好可自动保存。",
        "management": "负责查看当前角色的资料与管理能力。",
    }
    management_rule = (
        "用户要求改名时，必须调用 rename_persona；用户要求增加、更新或修改角色设定时，"
        "必须调用 update_persona_profile；用户要求删除资料时，必须调用 "
        "delete_persona_document。用户提供角色技能、经历、规则或其他较长事实资料时，必须调用 "
        "add_persona_knowledge 将其写入角色知识库；不要把角色资料保存为用户长期记忆。"
        "不得只口头宣称修改成功，也不要先追问确认；"
        "直接调用工具，由工具触发逐次确认。"
        if specialist == "management"
        else ""
    )
    return (
        f"你是 {context.persona_name} 的{specialist}能力专家。{identity_rule}"
        f"{duties[specialist]}{management_rule}角色设定：{profile}"
    )


def create_specialist(
    specialist: Specialist,
    context: PersonaAgentContext,
    model: BaseChatModel | None = None,
    checkpointer=None,
):
    return create_agent(
        model=model or get_llm(),
        tools=tools_for_specialist(specialist),
        system_prompt=specialist_prompt(specialist, context),
        context_schema=PersonaAgentContext,
        checkpointer=checkpointer,
        name=f"{specialist}_agent",
    )


def build_supervisor_graph(
    model: BaseChatModel | None = None,
    specialist_factory: Callable = create_specialist,
):
    builder = StateGraph(SupervisorState, context_schema=PersonaAgentContext)

    def supervisor_node(state: SupervisorState) -> dict:
        question = str(state["messages"][-1].content)
        return {"specialist": route_specialist(question)}

    def specialist_node(name: Specialist):
        def run(state: SupervisorState, runtime: Runtime[PersonaAgentContext]) -> dict:
            agent = specialist_factory(name, runtime.context, model or get_llm())
            result = agent.invoke({"messages": state["messages"]}, context=runtime.context)
            return {"messages": [result["messages"][-1]]}

        return run

    builder.add_node("supervisor", supervisor_node)
    for specialist in ("conversation", "web", "memory", "management"):
        builder.add_node(specialist, specialist_node(specialist))
        builder.add_edge(specialist, END)
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor",
        lambda state: state["specialist"],
        {name: name for name in ("conversation", "web", "memory", "management")},
    )
    return builder.compile()


def final_answer(state: dict) -> str:
    message = state["messages"][-1]
    return str(message.content) if isinstance(message, AIMessage) else str(message)
