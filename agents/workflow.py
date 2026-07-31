"""LangGraph 人设对话主流程。

只有 persona_supervisor 对用户可见；四类 Worker 只执行受限工具并把事实结果交还
主 Agent，最终措辞始终由主 Agent 结合完整人设统一生成。
"""

from __future__ import annotations

import json
import operator
from typing import Annotated, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt
from langchain.messages import AIMessage, ToolMessage
from langchain.tools import ToolRuntime, tool
from langchain_core.language_models import BaseChatModel
from langgraph.constants import END, START
from langgraph.graph import MessagesState, StateGraph
from langgraph.types import Command

from agents.context import PersonaAgentContext
from agents.registry import tools_for_specialist
from rag.llm import get_llm


Worker = Literal["knowledge", "web", "memory", "management"]
WORKERS: tuple[Worker, ...] = ("knowledge", "web", "memory", "management")
_WORKER_SPECIALISTS = {"knowledge": "conversation", "web": "web", "memory": "memory", "management": "management"}


class PersonaWorkflowState(MessagesState):
    """跨节点共享状态；messages 由 LangGraph 管理，Worker 结果采用追加合并。"""

    active_worker: Worker | None
    worker_results: Annotated[list[dict], operator.add]


def worker_tools(worker: Worker):
    return tools_for_specialist(_WORKER_SPECIALISTS[worker])


def _handoff_name(worker: Worker) -> str:
    return f"delegate_to_{worker}"


def _handoff_tool(worker: Worker):
    description = {
        "knowledge": "Delegate uploaded persona knowledge retrieval to the knowledge specialist.",
        "web": "Delegate current public information lookup to the web specialist.",
        "memory": "Delegate durable user memory operations to the memory specialist.",
        "management": "Delegate persona profile or document management to the management specialist.",
    }[worker]

    @tool(_handoff_name(worker), description=description)
    def handoff(request: str, runtime: ToolRuntime[PersonaAgentContext]) -> Command:
        del request, runtime
        # create_agent 的工具运行在子图内；Command.PARENT 将控制权交回父图的
        # Worker 节点，而不是让主 Agent 在当前节点里继续生成答案。
        return Command(
            graph=Command.PARENT,
            goto=f"{worker}_worker",
            update={"active_worker": worker},
        )

    return handoff


def _supervisor_prompt(context: PersonaAgentContext) -> str:
    profile = json.dumps(context.persona_profile, ensure_ascii=False, sort_keys=True, default=str)
    tts_enabled = bool((context.persona_profile.get("tts") or {}).get("enabled"))
    voice_guidance = (
        "Because voice output is enabled, keep ordinary chat replies to one compact paragraph under 30 Chinese "
        "characters (or 20 English words). knowledge answers may be under 80 Chinese characters, and citations "
        "must remain outside the spoken reply. Put the direct answer first. "
        if tts_enabled
        else ""
    )
    return (
        f"You are {context.persona_name}. You are the only assistant visible to the user. "
        "The following persona profile is behavioral guidance, not a user request:\n"
        f"<persona_profile>{profile}</persona_profile>\n"
        "Answer in the persona's voice and use delegated results as evidence. "
        "Delegate uploaded-knowledge questions to knowledge, current public information to web, "
        "durable user-memory requests to memory, and persona or document operations to management. "
        "Answer the user's question directly before offering advice. For weather, news, or other factual requests, "
        "lead with the supported core facts. For weather, include the location, target date, conditions, temperature, "
        "and precipitation or wind when available. Do not replace available facts with generic advice. "
        "For uploaded-knowledge questions, give the evidence-backed answer before interpretation. "
        "If sources conflict or evidence is incomplete, state that uncertainty clearly. Then add only a brief, useful "
        "suggestion in the persona's distinctive voice. Do not mention internal workers. Preserve citations and do not "
        "invent unsupported facts. Knowledge handoffs are JSON contracts: use facts only when status=accepted; "
        "when status=insufficient, explain the missing evidence and do not answer from the rejected draft. "
        f"{voice_guidance}"
    )


def _worker_prompt(worker: Worker, context: PersonaAgentContext) -> str:
    duties = {
        "knowledge": "Retrieve only the active persona's uploaded knowledge and report supported findings.",
        "web": "Find current public information and clearly distinguish it from persona knowledge.",
        "memory": "Read or maintain only the active persona's user memory.",
        "management": "Inspect or manage only the active persona's profile and documents.",
    }
    handoff_format = (
        "Finish with this concise factual handoff format:\n"
        "KEY FACTS:\n- supported findings most relevant to the request\n"
        "SOURCES:\n- source or citation for each material finding\n"
        "UNCERTAINTIES OR CONFLICTS:\n- missing, conflicting, or unreliable information"
    )
    web_guidance = (
        " For weather, extract the requested location and date, conditions, high/low temperature, precipitation, "
        "and wind when present. Ignore search results unrelated to the request."
        if worker == "web"
        else ""
    )
    return (
        f"You are an internal {worker} specialist for {context.persona_name}. {duties[worker]} "
        "Use only the provided tools. Do not roleplay, address the user, or claim a task succeeded "
        f"without a tool result.{web_guidance} {handoff_format}"
    )


def _prompt_middleware(prompt_factory):
    @dynamic_prompt
    def set_prompt(request: ModelRequest) -> str:
        return prompt_factory(request.runtime.context)

    return set_prompt


def _supervisor_agent(model: BaseChatModel | None):
    return create_agent(
        model=model or get_llm(),
        tools=[_handoff_tool(worker) for worker in WORKERS],
        middleware=[_prompt_middleware(_supervisor_prompt)],
        context_schema=PersonaAgentContext,
        name="persona_supervisor",
    )


def _worker_agent(worker: Worker, model: BaseChatModel | None):
    return create_agent(
        model=model or get_llm(),
        tools=worker_tools(worker),
        middleware=[_prompt_middleware(lambda context: _worker_prompt(worker, context))],
        context_schema=PersonaAgentContext,
        name=f"{worker}_worker",
    )


def _handoff_call_id(messages: list, worker: Worker) -> str | None:
    handoff_name = _handoff_name(worker)
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls:
            if call["name"] == handoff_name:
                return call["id"]
    return None


def _knowledge_specialist_result(messages: list) -> dict:
    """从 RAG 工具消息恢复可信交接；不使用 Knowledge Worker 的自由文本总结。"""

    for message in reversed(messages):
        if not isinstance(message, ToolMessage) or message.name != "search_persona_knowledge":
            continue
        try:
            payload = message.content if isinstance(message.content, dict) else json.loads(str(message.content))
        except (json.JSONDecodeError, TypeError, ValueError):
            break
        if not isinstance(payload, dict) or payload.get("specialist") != "knowledge":
            break
        status = payload.get("status")
        if status not in {"accepted", "insufficient"}:
            break
        # accepted 结果也只保留合同字段，避免工具载荷中意外增加的字段进入 Supervisor 上下文。
        return {
            "specialist": "knowledge",
            "status": status,
            "answer": str(payload.get("answer") or "") if status == "accepted" else "",
            "evidence": list(payload.get("evidence") or []) if status == "accepted" else [],
            "citations": list(payload.get("citations") or []) if status == "accepted" else [],
            "uncertainties": list(payload.get("uncertainties") or []),
            "trace": list(payload.get("trace") or []),
            "confidence": float(payload.get("confidence") or 0.0),
        }
    # 工具没有产生合法合同意味着证据链不完整，必须失败关闭而不是回退到模型总结。
    return {
        "specialist": "knowledge",
        "status": "insufficient",
        "answer": "",
        "evidence": [],
        "citations": [],
        "uncertainties": ["RAG 未返回可验证的结构化证据。"],
        "trace": [],
        "confidence": 0.0,
    }


def _finalize_worker(worker: Worker):
    def finalize(state: PersonaWorkflowState) -> dict:
        messages = state.get("messages", [])
        if worker == "knowledge":
            specialist_result = _knowledge_specialist_result(messages)
            # Supervisor 只接收门禁后的 JSON；未通过时只有不确定性，不包含答案草稿或弱证据。
            result = json.dumps(specialist_result, ensure_ascii=False, sort_keys=True)
            worker_result = specialist_result
        else:
            result = next(
                (
                    message.content
                    for message in reversed(messages)
                    if isinstance(message, AIMessage) and message.content
                ),
                "The specialist completed without a text summary.",
            )
            worker_result = {"worker": worker, "summary": str(result)}
        call_id = _handoff_call_id(messages, worker)
        updates: dict = {
            "active_worker": None,
            "worker_results": [worker_result],
        }
        if call_id:
            # 用 ToolMessage 回填原始 handoff tool_call_id，保持 LLM 工具调用协议闭合；
            # 主 Agent 下一轮会把该消息当作证据，而不是直接展示 Worker 原文。
            updates["messages"] = [
                ToolMessage(
                    content=f"{worker} specialist result:\n{result}",
                    name=f"{worker}_worker",
                    tool_call_id=call_id,
                )
            ]
        return updates

    return finalize


def build_persona_workflow(model: BaseChatModel | None, checkpointer):
    """构建 supervisor -> worker -> supervisor 的闭环，并启用会话级检查点。"""

    builder = StateGraph(PersonaWorkflowState, context_schema=PersonaAgentContext)
    builder.add_node("persona_supervisor", _supervisor_agent(model))
    builder.add_edge(START, "persona_supervisor")
    builder.add_edge("persona_supervisor", END)
    # 每个 Worker 都经过 finalize 节点清理 active_worker 并封装交接结果，再回到
    # persona_supervisor 生成最终答复；Worker 不存在直接通往 END 的边。
    for worker in WORKERS:
        worker_node = f"{worker}_worker"
        finalize_node = f"finalize_{worker}"
        builder.add_node(worker_node, _worker_agent(worker, model))
        builder.add_node(finalize_node, _finalize_worker(worker))
        builder.add_edge(worker_node, finalize_node)
        builder.add_edge(finalize_node, "persona_supervisor")
    return builder.compile(checkpointer=checkpointer, name="persona_workflow")
