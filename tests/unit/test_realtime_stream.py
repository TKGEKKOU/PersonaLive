import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from agents.context import PersonaAgentContext
from agents.service import PersonaAgentService
from realtime.execution import ConversationExecutionRegistry


def _context() -> PersonaAgentContext:
    return PersonaAgentContext(
        persona_id="persona-a",
        workspace_id="local-default",
        knowledge_space_ids=("space-a",),
        conversation_id="thread-a",
        persona_name="Ames",
        persona_type="character",
    )


class FakeGraph:
    def __init__(self, chunks, state, interrupts=()):
        self._chunks = chunks
        self._state = state
        self._interrupts = interrupts

    def stream(self, *args, **kwargs):
        for item in self._chunks:
            yield item

    def get_state(self, config):
        return SimpleNamespace(values=self._state, interrupts=self._interrupts)


def test_stream_query_yields_only_supervisor_tokens_and_result():
    graph = FakeGraph(
        chunks=[
            (
                ("persona_supervisor:x",),
                "messages",
                (AIMessage(content="第一句。"), {"lc_agent_name": "persona_supervisor"}),
            ),
            (
                ("persona_supervisor:x",),
                "messages",
                (AIMessage(content="第二句。"), {"lc_agent_name": "persona_supervisor"}),
            ),
            (
                ("web_worker:x",),
                "messages",
                (AIMessage(content="内部交接文本"), {"lc_agent_name": "web_worker"}),
            ),
            (("web_worker:x",), "updates", {"web_worker": {}}),
        ],
        state={
            "messages": [AIMessage(content="第一句。第二句。")],
            "active_worker": None,
            "loaded_skills": [],
        },
    )
    service = PersonaAgentService(checkpointer=object())
    service._workflow = graph

    events = list(service.stream_query("普通问题", _context()))
    tokens = [event["text"] for event in events if event["kind"] == "token"]
    assert tokens == ["第一句。", "第二句。"]
    assert [event["stage"] for event in events if event["kind"] == "stage"] == [
        "联网agent · 正在搜索…"
    ]
    result = events[-1]["result"]
    assert result.status == "completed"
    assert result.answer == "第一句。第二句。"


def test_stream_query_returns_pending_confirmation_without_tokens():
    graph = FakeGraph(
        chunks=[],
        state={
            "messages": [],
            "active_worker": "web",
            "loaded_skills": [],
            "__interrupt__": (SimpleNamespace(value={"tool": "web_search"}),),
        },
    )
    service = PersonaAgentService(checkpointer=object())
    service._workflow = graph

    events = list(service.stream_query("问题", _context()))
    assert [event for event in events if event["kind"] == "token"] == []
    assert events[-1]["result"].status == "pending_confirmation"


def test_run_stream_forwards_events_and_stops():
    async def consume():
        registry = ConversationExecutionRegistry()
        events = []
        async for event in registry.run_stream(
            "thread-a",
            lambda: iter(
                [
                    {"kind": "token", "text": "你好"},
                    {"kind": "result", "result": "done"},
                ]
            ),
        ):
            events.append(event)
        return events

    assert asyncio.run(consume()) == [
        {"kind": "token", "text": "你好"},
        {"kind": "result", "result": "done"},
    ]


def test_run_stream_propagates_worker_error():
    def boom():
        yield {"kind": "token", "text": "x"}
        raise RuntimeError("boom")

    async def consume():
        registry = ConversationExecutionRegistry()
        with pytest.raises(RuntimeError, match="boom"):
            async for _event in registry.run_stream("thread-a", boom):
                pass

    asyncio.run(consume())
