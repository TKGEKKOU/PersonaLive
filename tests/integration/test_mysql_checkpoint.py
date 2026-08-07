import os
from uuid import uuid4

import pytest
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from agents.checkpoint import create_sqlite_checkpointer
from settings import Settings


class CheckpointState(TypedDict, total=False):
    value: str


def gate(state: CheckpointState) -> CheckpointState:
    decision = interrupt({"tool": "test_mutation"})
    return {"value": "resumed" if decision.get("approved") else "cancelled"}


def checkpoint_graph(saver):
    builder = StateGraph(CheckpointState)
    builder.add_node("gate", gate)
    builder.add_edge(START, "gate")
    builder.add_edge("gate", END)
    return builder.compile(checkpointer=saver)


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_MYSQL_CHECKPOINT_INTEGRATION") != "1",
    reason="set RUN_MYSQL_CHECKPOINT_INTEGRATION=1 to test restart persistence",
)
def test_mysql_checkpoint_restores_pending_interrupt_after_reconnect():
    config = {"configurable": {"thread_id": f"restart-test-{uuid4().hex}"}}
    first = create_sqlite_checkpointer(Settings.load())
    try:
        pending = checkpoint_graph(first.saver).invoke({"value": ""}, config)
        assert pending["__interrupt__"]
    finally:
        first.close()

    second = create_sqlite_checkpointer(Settings.load())
    try:
        resumed = checkpoint_graph(second.saver).invoke(
            Command(resume={"approved": True}), config
        )
        assert resumed["value"] == "resumed"
    finally:
        second.close()
