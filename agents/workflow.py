"""LangGraph 人设对话主流程。

只有 persona_supervisor 对用户可见；四类 Worker 只执行受限工具并把事实结果交还
主 Agent，最终措辞始终由主 Agent 结合完整人设统一生成。
"""

from __future__ import annotations

import json
import operator
from typing import Annotated, Callable, Literal

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest, dynamic_prompt, wrap_model_call
from langchain.messages import AIMessage, SystemMessage, ToolMessage
from langchain.tools import ToolRuntime, tool
from langchain_core.language_models import BaseChatModel
from langgraph.constants import END, START
from langgraph.graph import MessagesState, StateGraph
from langgraph.types import Command

from agents.context import PersonaAgentContext
from agents.mcp_grants import is_mcp_tool_visible
from agents.registry import tools_for_specialist
from agents.skills import get_skill, list_skills, load_skill, tools_for_skill
from agents.tools.memory import memories_for_context
from rag.llm import get_llm


Worker = Literal["knowledge", "web", "memory", "management"]
WORKERS: tuple[Worker, ...] = ("knowledge", "web", "memory", "management")
_WORKER_SPECIALISTS = {"knowledge": "conversation", "web": "web", "memory": "memory", "management": "management"}


class PersonaWorkflowState(MessagesState):
    """跨节点共享状态；messages 由 LangGraph 管理，Worker 结果采用追加合并。"""

    active_worker: Worker | None
    worker_results: Annotated[list[dict], operator.add]
    loaded_skills: Annotated[list[str], operator.add]


class SupervisorAgentState(MessagesState):
    """Supervisor 子图状态：只声明 messages 与 loaded_skills。

    刻意不继承 PersonaWorkflowState——子图若把未修改的 worker_results 等字段
    原样输出，父图 reducer 会把同一份值再次合并导致重复；子图只需回传
    loaded_skills（load_skill 工具写入）即可。
    """

    loaded_skills: Annotated[list[str], operator.add]


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
    memory_block = ""
    try:
        memories = memories_for_context(context)
        if memories:
            lines = "\n".join(f"- {memory['content']}" for memory in memories)
            memory_block = (
                "\nThe following are the user's durable memories for this persona; "
                "recall them naturally in conversation whenever relevant:\n"
                f"<persona_memories>{lines}</persona_memories>\n"
            )
    except Exception:
        # Memory loading must never block or break a turn (e.g. no DB session).
        memory_block = ""
    tts_enabled = bool((context.persona_profile.get("tts") or {}).get("enabled"))
    reply_guidance = (
        "Keep ordinary chat replies around 30 Chinese characters, preferring fewer and never exceeding 50 "
        "(roughly 20 English words, never exceeding 30). For knowledge, web, or memory answers, lead with the "
        "direct evidence-backed answer and keep it concise; put citations outside the reply body when possible. "
    )
    voice_guidance = (
        "The reply may be read aloud by voice synthesis, so keep it short, complete, and accurate in one breath. "
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
        f"{memory_block}"
        "Answer the user's question directly before offering advice. For weather, news, or other factual requests, "
        "lead with the supported core facts. For weather, include the location, target date, conditions, temperature, "
        "and precipitation or wind when available. Do not replace available facts with generic advice. "
        "For uploaded-knowledge questions, give the evidence-backed answer before interpretation. "
        "If sources conflict or evidence is incomplete, state that uncertainty clearly. Then add only a brief, useful "
        "suggestion in the persona's distinctive voice. Do not mention internal workers. Preserve citations and do not "
        "invent unsupported facts. Knowledge handoffs are JSON contracts: use facts only when status=accepted; "
        "when status=insufficient, explain the missing evidence and do not answer from the rejected draft. "
        f"{reply_guidance}{voice_guidance}"
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


def build_skill_middleware(base_tools: list):
    """构建"按需加载"工具中间件：基础工具 + 已加载 skill 的工具。

    wrap_model_call 钩子在每次模型调用前执行。create_agent 的 ToolNode 需要
    注册全部工具才能执行它们，但模型实际看到哪些由 request.tools 决定——
    这里始终把可见工具收敛为"基础工具（handoff + load_skill）+ 已加载技能的
    工具"，未加载任何 skill 时不暴露任何技能工具，从源头缓解工具过载。
    """

    base_names = {tool.name for tool in base_tools}

    @wrap_model_call
    def skill_middleware(request: ModelRequest, handler: Callable) -> ModelRequest:
        loaded = request.state.get("loaded_skills") or []
        persona_id = getattr(getattr(request.runtime, "context", None), "persona_id", "")
        visible = [
            tool
            for tool in request.tools
            if isinstance(tool, dict) or tool.name in base_names
        ]
        visible_names = {getattr(tool, "name", None) for tool in visible}
        prompt_parts: list[str] = []
        if request.system_message is not None and request.system_message.content:
            prompt_parts.append(str(request.system_message.content))
        for skill_name in loaded:
            try:
                skill = get_skill(skill_name)
            except KeyError:
                continue
            # 技能提示词包：加载后拼进 system prompt，让模型获得领域行为约束。
            if skill.instructions:
                prompt_parts.append(f"<skill:{skill.name}>\n{skill.instructions}\n</skill>")
            for skill_tool in tools_for_skill(skill):
                if (
                    skill_tool.name not in visible_names
                    and is_mcp_tool_visible(persona_id, skill_tool.name)
                ):
                    visible.append(skill_tool)
                    visible_names.add(skill_tool.name)
        system_message = (
            SystemMessage(content="\n\n".join(prompt_parts))
            if prompt_parts
            else request.system_message
        )
        return handler(request.override(tools=visible, system_message=system_message))

    return skill_middleware


def _supervisor_agent(model: BaseChatModel | None):
    # LangChain 1.0 的 create_agent 高阶入口：把「模型调用 -> 工具决策 -> 执行 -> 结果整合」
    # 闭环封装为 LangGraph 子图，开发者只需提供模型、工具和 system prompt。
    # 本项目的"工具"是四个 handoff（delegate_to_*）：Supervisor 不直接干活，
    # 而是把任务转交给对应 Worker 节点，由 Worker 用受限工具集执行后再交回。
    base_tools = [_handoff_tool(worker) for worker in WORKERS] + [load_skill]
    # 全部 skill 工具注册进 ToolNode（可执行），但默认不暴露给模型；
    # 可见性由 build_skill_middleware 按 loaded_skills 状态动态收敛。
    skill_tools = {
        skill_tool.name: skill_tool
        for skill in list_skills()
        for skill_tool in tools_for_skill(skill)
    }
    return create_agent(
        model=model or get_llm(),
        tools=base_tools + list(skill_tools.values()),
        # 子图直接复用父图状态模式：load_skill 写入的 loaded_skills 会随子图
        # 输出合并回父图并被 checkpointer 持久化，跨轮次技能状态不丢失。
        state_schema=SupervisorAgentState,
        # middleware 里的 dynamic_prompt 每次请求动态生成人设 prompt（注入完整人设
        # profile 与持久记忆），而不必为每种角色手写静态模板——这是 LangChain 1.0
        # 中间件机制的典型用法：钩入模型调用前，不改 Agent 核心逻辑。
        # 顺序敏感：dynamic_prompt 与 skill_middleware 都是 wrap_model_call 链，
        # 后面的执行时覆盖 system_message——所以技能注入必须排在提示词注入之后，
        # 才能读到已注入的人设 prompt 再追加技能 instructions。
        middleware=[_prompt_middleware(_supervisor_prompt), build_skill_middleware(base_tools)],
        # context_schema 把 PersonaAgentContext（角色/会话上下文）作为不可变上下文
        # 传给工具运行时，Worker 工具据此做作用域过滤，而不是塞进对话消息里。
        context_schema=PersonaAgentContext,
        name="persona_supervisor",
    )


def _worker_agent(worker: Worker, model: BaseChatModel | None):
    # 每个 Worker 是独立的 create_agent，只挂自己那一类受限工具
    # （knowledge 只有 RAG 检索工具，management 只有文档/人设管理工具等），
    # 从工具集层面强制最小权限，防止 Worker 越权调用其他领域能力。
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
    """构建 supervisor -> worker -> supervisor 的闭环，并启用会话级检查点。

    设计要点：
    - 只有 persona_supervisor 对用户可见：它是唯一直接生成最终回复的节点，
      Worker 永远不直接回答用户，只能把事实性结果交回 Supervisor 整合。
    - Worker 通过 handoff 工具（Command(PARENT, goto=worker_node)）把控制权从
      Supervisor 子图交回父图对应节点；执行完再由 finalize 节点封装结果回 Supervisor。
    - checkpointer 按 thread_id（persona_id:conversation_id）持久化整张图状态，
      因此中断（interrupt）恢复、多轮对话、服务重启都能从检查点续跑。
    """

    builder = StateGraph(PersonaWorkflowState, context_schema=PersonaAgentContext)
    builder.add_node("persona_supervisor", _supervisor_agent(model))
    builder.add_edge(START, "persona_supervisor")
    builder.add_edge("persona_supervisor", END)
    # 每个 Worker 都经过 finalize 节点：清理 active_worker、把 Worker 的原始输出封装成
    # 结构化交接结果（knowledge 走 JSON 合同，其余走文本摘要），再回到 persona_supervisor
    # 生成最终答复；图中不存在 Worker 直达 END 的边，保证所有对外回复都过 Supervisor。
    for worker in WORKERS:
        worker_node = f"{worker}_worker"
        finalize_node = f"finalize_{worker}"
        builder.add_node(worker_node, _worker_agent(worker, model))
        builder.add_node(finalize_node, _finalize_worker(worker))
        builder.add_edge(worker_node, finalize_node)
        builder.add_edge(finalize_node, "persona_supervisor")
    return builder.compile(checkpointer=checkpointer, name="persona_workflow")
