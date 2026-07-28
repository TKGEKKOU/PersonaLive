import json
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command

from agents.context import PersonaAgentContext
from agents.registry import capability_summary, specialist_for_tool, tool_specs
from agents.supervisor import Specialist
from agents.workflow import build_persona_workflow
from rag.llm import get_llm


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


class PersonaAgentService:
    def __init__(
        self,
        checkpointer: BaseCheckpointSaver,
        model: BaseChatModel | None = None,
    ) -> None:
        self.checkpointer = checkpointer
        self.model = model
        self._workflow = None

    @staticmethod
    def thread_id(context: PersonaAgentContext, specialist: Specialist) -> str:
        del specialist
        return f"{context.persona_id}:{context.conversation_id}"

    def _graph(self):
        if self._workflow is None:
            self._workflow = build_persona_workflow(
                self.model or get_llm(),
                self.checkpointer,
            )
        return self._workflow

    def _config(self, context: PersonaAgentContext) -> dict:
        return {"configurable": {"thread_id": self.thread_id(context, "conversation")}}

    def query(self, question: str, context: PersonaAgentContext) -> AgentTurnResult:
        graph = self._graph()
        pending = self._find_pending(graph, context)
        if pending is not None:
            return pending
        if is_capability_question(question):
            return AgentTurnResult(
                status="completed",
                answer=capability_summary(),
                specialist="conversation",
            )
        result = graph.invoke(
            {"messages": [{"role": "user", "content": question}], "active_worker": None},
            self._config(context),
            context=context,
        )
        return self._result(result)

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
        del specialist
        graph = self._graph()
        config = self._config(context)
        snapshot = graph.get_state(config)
        if not snapshot.interrupts:
            return self._result(snapshot.values or {})
        result = graph.invoke(
            Command(resume={"approved": approved}),
            config,
            context=context,
        )
        return self._result(result)

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
    def _result(state: dict) -> AgentTurnResult:
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
        )

    @staticmethod
    def _tool_payload(content: Any) -> Any:
        if not isinstance(content, str):
            return content
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return content
