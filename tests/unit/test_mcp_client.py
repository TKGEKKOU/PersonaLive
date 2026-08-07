"""MCP 客户端管理器：工具分类、注册与连接状态。"""

import asyncio

from langchain_core.tools import tool

from agents.registry import tool_specs, unregister_tool_specs
from integrations.mcp.client import (
    MCPManager,
    classify_mcp_tool,
)
from integrations.mcp.config import MCPServerConfig


def test_config_roundtrip_allowed_persona_ids(tmp_path):
    from integrations.mcp.config import load_servers, save_servers

    cfg = MCPServerConfig(
        name="demo", command="python", args=["s.py"], allowed_persona_ids=["p1", "p2"]
    )
    save_servers(tmp_path / "mcp_servers.json", [cfg])
    loaded = load_servers(tmp_path / "mcp_servers.json")[0]
    assert loaded.allowed_persona_ids == ["p1", "p2"]


def test_tool_spec_server_default_empty():
    from agents.registry import ToolSpec

    spec = ToolSpec("n", "mcp", tool=lambda: None)
    assert spec.server == ""


def _make_tool(name, description="desc", metadata=None):
    def fn(*args, **kwargs):
        return "ok"

    fn.__name__ = name
    built = tool(description=description)(fn)
    built.metadata = metadata
    return built


def test_classify_mcp_tool():
    read_only = _make_tool("read", metadata={"read_only_hint": True})
    assert classify_mcp_tool(read_only) == (False, False)

    writer = _make_tool("write", metadata={"read_only_hint": False})
    assert classify_mcp_tool(writer) == (True, True)

    destructive = _make_tool("rm", metadata={"destructive_hint": True})
    assert classify_mcp_tool(destructive) == (True, True)

    undeclared = _make_tool("plain")
    assert classify_mcp_tool(undeclared) == (True, True)

    camel = _make_tool("camel", metadata={"readOnlyHint": True})
    assert classify_mcp_tool(camel) == (False, False)


class FakeMCPClient:
    """假客户端：按服务器名返回预设工具，可配置失败。"""

    def __init__(self, connections, tool_name_prefix=True, handle_tool_errors=True):
        self.connections = connections
        self.tool_name_prefix = tool_name_prefix
        self.server_tools = {}

    async def get_tools(self, server_name=None):
        if self.connections.get(server_name, {}).get("fail"):
            raise ConnectionError("boom")
        return self.server_tools.get(server_name, [])


def _fake_factory(tools_by_server, fail_servers=()):
    def factory(connections, tool_name_prefix=True, handle_tool_errors=True):
        client = FakeMCPClient(connections, tool_name_prefix, handle_tool_errors)
        for name, cfg in connections.items():
            cfg["fail"] = name in fail_servers
            client.server_tools[name] = tools_by_server.get(name, [])
        return client

    return factory


def test_register_and_unregister_tool_specs():
    before = {spec.name for spec in tool_specs()}
    extra = [
        _make_tool("demo_add", metadata={"read_only_hint": True}),
        _make_tool("demo_write", metadata={"read_only_hint": False}),
    ]
    from agents.registry import ToolSpec, register_tool_specs

    specs = [
        ToolSpec(
            name=tool.name,
            specialist="mcp",
            tool=tool,
            requires_confirmation=cf,
            mutates_data=mut,
        )
        for tool, (cf, mut) in zip(extra, [classify_mcp_tool(t) for t in extra])
    ]
    register_tool_specs(specs)
    names = {spec.name for spec in tool_specs()}
    assert names >= before | {"demo_add", "demo_write"}
    unregister_tool_specs(["demo_add", "demo_write"])
    assert {spec.name for spec in tool_specs()} == before


def test_connect_all_registers_tools_and_status(tmp_path):
    tools = [
        _make_tool("demo_add", metadata={"read_only_hint": True}),
        _make_tool("demo_write", metadata={"read_only_hint": False}),
    ]
    manager = MCPManager(
        tmp_path / "mcp_servers.json",
        client_factory=_fake_factory({"demo": tools}, fail_servers=("bad",)),
    )
    manager.save_configs(
        [
            MCPServerConfig(name="demo", command="python", args=["s.py"]),
            MCPServerConfig(name="bad", command="python", args=["s.py"]),
            MCPServerConfig(name="off", command="python", args=["s.py"], enabled=False),
        ]
    )
    status = asyncio.run(manager.connect_all(register=True))
    assert status["demo"]["status"] == "connected"
    assert status["demo"]["tool_count"] == 2
    assert status["bad"]["status"] == "error"
    assert status["off"]["status"] == "disabled"
    assert {info.name for info in manager.registered_tools()} == {
        "demo_add",
        "demo_write",
    }
    spec_by_name = {spec.name: spec for spec in tool_specs()}
    assert spec_by_name["demo_add"].specialist == "mcp"
    assert spec_by_name["demo_add"].requires_confirmation is False
    assert spec_by_name["demo_write"].requires_confirmation is True
    manager.unregister_all()
    assert manager.registered_tools() == []


def test_mcp_tools_not_exposed_to_workers(tmp_path):
    """specialist='mcp' 的工具不应出现在任何 Worker 的工具集里。"""

    from agents.registry import tools_for_specialist

    tools = [_make_tool("demo_add")]
    manager = MCPManager(
        tmp_path / "mcp_servers.json",
        client_factory=_fake_factory({"demo": tools}),
    )
    manager.save_configs([MCPServerConfig(name="demo", command="python")])
    asyncio.run(manager.connect_all(register=True))
    try:
        worker_names = {
            tool.name
            for specialist in ("conversation", "web", "memory", "management")
            for tool in tools_for_specialist(specialist)
        }
        assert "demo_add" not in worker_names
    finally:
        manager.unregister_all()


def test_client_factory_disables_tool_name_prefix(tmp_path):
    """MCP 工具名应保持服务器原生名，便于标准技能包直接引用。"""

    observed: list[bool] = []

    def recording_factory(connections, tool_name_prefix=True, handle_tool_errors=True):
        observed.append(tool_name_prefix)
        return FakeMCPClient(connections, tool_name_prefix, handle_tool_errors)

    manager = MCPManager(tmp_path / "mcp_servers.json", client_factory=recording_factory)
    manager.save_configs([MCPServerConfig(name="demo", command="python", args=["s.py"])])
    asyncio.run(manager.connect_all(register=True))
    assert observed and all(value is False for value in observed)
    manager.unregister_all()


def test_mcp_tools_are_sync_invokable(tmp_path):
    """异步 MCP 工具注册后可在同步 Agent 链路中调用（asyncio.run 桥接）。"""

    from langchain_core.tools import tool as make_tool
    from agents.registry import tool_specs

    @make_tool
    async def async_search(query: str) -> str:
        """Search the web."""
        return f"result:{query}"

    manager = MCPManager(
        tmp_path / "mcp_servers.json",
        client_factory=_fake_factory({"demo": [async_search]}),
    )
    manager.save_configs([MCPServerConfig(name="demo", command="python")])
    asyncio.run(manager.connect_all(register=True))
    try:
        spec = next(s for s in tool_specs() if s.name == "async_search")
        assert spec.tool.invoke({"query": "天气"}) == "result:天气"
    finally:
        manager.unregister_all()


def test_mcp_tool_call_has_timeout_bridge(tmp_path, monkeypatch):
    """MCP 工具调用有兜底超时，挂起时不会无限阻塞整轮对话。"""

    from langchain_core.tools import tool as make_tool
    from integrations.mcp import client as mcp_client
    from agents.registry import tool_specs

    @make_tool
    async def slow_search(query: str) -> str:
        """Search slowly."""
        await asyncio.sleep(10)
        return "never"

    monkeypatch.setattr(mcp_client, "MCP_TOOL_TIMEOUT_SECONDS", 0.1)
    manager = MCPManager(
        tmp_path / "mcp_servers.json",
        client_factory=_fake_factory({"demo": [slow_search]}),
    )
    manager.save_configs([MCPServerConfig(name="demo", command="python")])
    asyncio.run(manager.connect_all(register=True))
    try:
        spec = next(s for s in tool_specs() if s.name == "slow_search")
        import pytest

        with pytest.raises(Exception):
            spec.tool.invoke({"query": "x"})
    finally:
        manager.unregister_all()


def test_enable_disable_reload_lifecycle(tmp_path):
    tools = [_make_tool("demo_add")]
    manager = MCPManager(
        tmp_path / "mcp_servers.json",
        client_factory=_fake_factory({"demo": tools}),
    )
    cfg = MCPServerConfig(name="demo", command="python", args=["s.py"])
    status = asyncio.run(manager.enable_server(cfg))
    assert status["status"] == "connected"
    assert {info.name for info in manager.registered_tools()} == {"demo_add"}
    assert manager.disable_server("demo")["status"] == "disabled"
    assert manager.registered_tools() == []
    assert manager.status()["demo"]["last_check"]


def test_disable_only_removes_own_server(tmp_path):
    manager = MCPManager(
        tmp_path / "mcp_servers.json",
        client_factory=_fake_factory(
            {"a": [_make_tool("tool_a")], "b": [_make_tool("tool_b")]}
        ),
    )
    manager.save_configs(
        [
            MCPServerConfig(name="a", command="python", args=["s.py"]),
            MCPServerConfig(name="b", command="python", args=["s.py"]),
        ]
    )
    asyncio.run(manager.connect_all(register=True))
    manager.disable_server("a")
    assert {info.name for info in manager.registered_tools()} == {"tool_b"}
    manager.unregister_all()
