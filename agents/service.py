import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from agents.context import PersonaAgentContext
from agents import registry as tool_registry
from agents.registry import capability_summary, specialist_for_tool, tool_specs
from agents.supervisor import Specialist
from agents.workflow import build_persona_workflow
from rag.llm import LLM_UNAVAILABLE_MESSAGE, get_llm, is_transient_provider_error


logger = logging.getLogger(__name__)


CAPABILITY_QUESTION_SIGNALS = (
    "tool",
    "工具",
    "有哪些能力",
    "有什么能力",
    "你会什么",
    "会调用什么",
    "会调用哪些",
    "能调用什么",
    "能调用哪些",
    "可以调用什么",
    "可以调用哪些",
)


_STAGE_BY_NODE = {
    "persona_supervisor": "正在思考…",
    "knowledge_worker": "知识agent · 正在检索角色资料…",
    "web_worker": "联网agent · 正在搜索…",
    "memory_worker": "记忆agent · 正在读取记忆…",
    "management_worker": "管理agent · 正在处理角色资料…",
}


def _stage_from_update(update: dict) -> str | None:
    """把 LangGraph 节点更新映射为阶段文案；无匹配返回 None。"""
    for key in update:
        for node, label in _STAGE_BY_NODE.items():
            if node in str(key):
                return label
    return None


def is_capability_question(question: str) -> bool:
    normalized = question.strip().lower()
    return any(signal in normalized for signal in CAPABILITY_QUESTION_SIGNALS)


@dataclass(frozen=True)
class AgentTurnResult:
    status: str
    answer: str
    specialist: Specialist
    pending_action: dict[str, Any] | None = None
    tool_calls: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    trace: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    duration_seconds: float = 0.0
    loaded_skills: tuple[str, ...] = field(default_factory=tuple)


class PersonaAgentService:
    """LangGraph 的应用层入口，负责线程隔离、暂停确认和结果归一化。"""

    def __init__(
        self,
        checkpointer: BaseCheckpointSaver,
        model: BaseChatModel | None = None,
    ) -> None:
        self.checkpointer = checkpointer
        self.model = model
        self._workflow = None
        self._workflow_registry_revision = tool_registry.tool_registry_revision()

    @staticmethod
    def thread_id(context: PersonaAgentContext, specialist: Specialist) -> str:
        # 所有 Worker 共享同一条角色会话线程，确保 handoff、interrupt 和 resume
        # 都能恢复到同一份父图检查点。specialist 参数仅为兼容旧接口保留。
        del specialist
        return f"{context.persona_id}:{context.conversation_id}"

    def _graph(self):
        revision = tool_registry.tool_registry_revision()
        if self._workflow is None or self._workflow_registry_revision != revision:
            self._workflow = build_persona_workflow(
                self.model or get_llm(),
                self.checkpointer,
            )
            self._workflow_registry_revision = revision
        return self._workflow

    def _config(self, context: PersonaAgentContext) -> dict:
        return {"configurable": {"thread_id": self.thread_id(context, "conversation")}}

    def query(self, question: str, context: PersonaAgentContext) -> AgentTurnResult:
        graph = self._graph()
        started = time.perf_counter()
        # 已暂停的写操作必须先由用户处理；不能用新问题绕过上一次确认。
        pending = self._find_pending(graph, context)
        if pending is not None:
            return pending
        if is_capability_question(question):
            return AgentTurnResult(
                status="completed",
                answer=capability_summary(),
                specialist="conversation",
            )
        try:
            result = graph.invoke(
                {"messages": [{"role": "user", "content": question}], "active_worker": None},
                self._config(context),
                context=context,
            )
        except Exception as exc:
            # 模型服务瞬时不可用（429/5xx）时返回统一降级提示，避免整个请求 500；
            # 其他异常仍然上抛，便于定位真实故障。
            if is_transient_provider_error(exc):
                logger.warning("Agent LLM 服务瞬时故障，返回降级提示：%s", exc)
                return AgentTurnResult(
                    status="completed",
                    answer=LLM_UNAVAILABLE_MESSAGE,
                    specialist="conversation",
                    duration_seconds=time.perf_counter() - started,
                )
            raise
        return self._result(result, time.perf_counter() - started)

    def stream_query(
        self,
        question: str,
        context: PersonaAgentContext,
    ):
        """流式查询：边生成边产出 token 事件，最后产出完整结果事件。

        事件格式：
          {"kind": "token", "text": "<增量文本>"}
          {"kind": "result", "result": AgentTurnResult}

        只转发 persona_supervisor（唯一对用户可见的节点）的模型输出，
        Worker 子图的内部生成会被过滤，避免内部交接文本泄漏到对话页。
        """
        graph = self._graph()
        pending = self._find_pending(graph, context)
        if pending is not None:
            yield {"kind": "result", "result": pending}
            return
        if is_capability_question(question):
            yield {
                "kind": "result",
                "result": AgentTurnResult(
                    status="completed",
                    answer=capability_summary(),
                    specialist="conversation",
                ),
            }
            return
        config = self._config(context)
        started = time.perf_counter()
        failed = False
        last_stage: str | None = None
        try:
            for _namespace, mode, payload in graph.stream(
                {"messages": [{"role": "user", "content": question}], "active_worker": None},
                config,
                stream_mode=["messages", "updates"],
                subgraphs=True,
                context=context,
            ):
                if mode == "messages":
                    chunk, metadata = payload
                    if metadata.get("lc_agent_name") != "persona_supervisor":
                        continue
                    content = getattr(chunk, "content", None)
                    if isinstance(content, str) and content:
                        yield {"kind": "token", "text": content}
                elif mode == "updates":
                    stage = _stage_from_update(payload)
                    if stage and stage != last_stage:
                        last_stage = stage
                        yield {"kind": "stage", "stage": stage}
        except Exception as exc:
            if not is_transient_provider_error(exc):
                raise
            logger.warning("Agent 流式查询时 LLM 服务瞬时故障，返回降级提示：%s", exc)
            failed = True
        if failed:
            yield {
                "kind": "result",
                "result": AgentTurnResult(
                    status="completed",
                    answer=LLM_UNAVAILABLE_MESSAGE,
                    specialist="conversation",
                    duration_seconds=time.perf_counter() - started,
                ),
            }
            return
        state = graph.get_state(config)
        yield {
            "kind": "result",
            "result": self._result(state.values or {}, time.perf_counter() - started),
        }

    def stream_resume(
        self,
        context: PersonaAgentContext,
        specialist: Specialist,
        approved: bool,
    ):
        """流式恢复被中断的确认回合，事件格式与 stream_query 一致。"""
        graph = self._graph()
        config = self._config(context)
        started = time.perf_counter()
        snapshot = graph.get_state(config)
        if not snapshot.interrupts:
            yield {
                "kind": "result",
                "result": self._result(snapshot.values or {}, time.perf_counter() - started),
            }
            return
        failed = False
        last_stage: str | None = None
        try:
            for _namespace, mode, payload in graph.stream(
                Command(resume={"approved": approved}),
                config,
                stream_mode=["messages", "updates"],
                subgraphs=True,
                context=context,
            ):
                if mode == "messages":
                    chunk, metadata = payload
                    if metadata.get("lc_agent_name") != "persona_supervisor":
                        continue
                    content = getattr(chunk, "content", None)
                    if isinstance(content, str) and content:
                        yield {"kind": "token", "text": content}
                elif mode == "updates":
                    stage = _stage_from_update(payload)
                    if stage and stage != last_stage:
                        last_stage = stage
                        yield {"kind": "stage", "stage": stage}
        except Exception as exc:
            if not is_transient_provider_error(exc):
                raise
            logger.warning("Agent 流式恢复时 LLM 服务瞬时故障，返回降级提示：%s", exc)
            failed = True
        if failed:
            yield {
                "kind": "result",
                "result": AgentTurnResult(
                    status="completed",
                    answer=LLM_UNAVAILABLE_MESSAGE,
                    specialist="conversation",
                    duration_seconds=time.perf_counter() - started,
                ),
            }
            return
        state = graph.get_state(config)
        yield {
            "kind": "result",
            "result": self._result(state.values or {}, time.perf_counter() - started),
        }

    def _find_pending(self, graph, context: PersonaAgentContext) -> AgentTurnResult | None:
        snapshot = graph.get_state(self._config(context))
        if snapshot.interrupts:
            action = snapshot.interrupts[0].value
            return AgentTurnResult(
                status="pending_confirmation",
                answer="",
                specialist=self._specialist_for_state(snapshot.values or {}, action),
                pending_action=action,
            )
        return None

    def resume(
        self,
        context: PersonaAgentContext,
        specialist: Specialist,
        approved: bool,
    ) -> AgentTurnResult:
        started = time.perf_counter()
        del specialist
        graph = self._graph()
        config = self._config(context)
        snapshot = graph.get_state(config)
        if not snapshot.interrupts:
            return self._result(snapshot.values or {}, time.perf_counter() - started)
        # Command(resume=...) 从 checkpointer 中恢复 interrupt 所在节点，不会重跑
        # 用户消息之前已经完成的 Worker 步骤。
        try:
            result = graph.invoke(
                Command(resume={"approved": approved}),
                config,
                context=context,
            )
        except Exception as exc:
            if is_transient_provider_error(exc):
                logger.warning("Agent 恢复会话时 LLM 服务瞬时故障，返回降级提示：%s", exc)
                return AgentTurnResult(
                    status="completed",
                    answer=LLM_UNAVAILABLE_MESSAGE,
                    specialist="conversation",
                    duration_seconds=time.perf_counter() - started,
                )
            raise
        return self._result(result, time.perf_counter() - started)

    @staticmethod
    def _specialist_for_state(state: dict, action: dict | None = None) -> Specialist:
        worker = state.get("active_worker")
        if worker == "knowledge":
            return "conversation"
        if worker in {"web", "memory", "management"}:
            return worker
        if action:
            return specialist_for_tool(str(action.get("tool", "")))
        return "conversation"

    @staticmethod
    def _result(state: dict, duration_seconds: float = 0.0) -> AgentTurnResult:
        """只暴露注册工具的结果，过滤内部 handoff ToolMessage。"""

        interrupts = state.get("__interrupt__") or ()
        if interrupts:
            return AgentTurnResult(
                status="pending_confirmation",
                answer="",
                specialist=PersonaAgentService._specialist_for_state(state, interrupts[0].value),
                pending_action=interrupts[0].value,
            )
        messages = state.get("messages") or []
        answer = ""
        tool_calls: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = []
        loaded_skills = list(state.get("loaded_skills") or [])
        visible_tools = {spec.name for spec in tool_specs()}
        for message in messages:
            if isinstance(message, AIMessage) and message.content:
                answer = str(message.content)
            if not isinstance(message, ToolMessage):
                continue
            payload = PersonaAgentService._tool_payload(message.content)
            if message.name not in visible_tools:
                continue
            tool_calls.append({"name": message.name, "result": payload})
            if message.name == "search_persona_knowledge" and isinstance(payload, dict):
                evidence = list(payload.get("evidence") or [])
                trace = list(payload.get("trace") or [])
        return AgentTurnResult(
            status="completed",
            answer=answer,
            specialist=PersonaAgentService._specialist_for_state(state),
            tool_calls=tuple(tool_calls[-8:]),
            evidence=tuple(evidence),
            trace=tuple(trace),
            duration_seconds=duration_seconds,
            loaded_skills=tuple(loaded_skills),
        )

    @staticmethod
    def _tool_payload(content: Any) -> Any:
        if not isinstance(content, str):
            return content
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
