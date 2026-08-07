"""角色 MCP 授权 API 测试。"""

import pytest
from langchain_core.tools import tool

from integrations.mcp.client import MCPManager


def _fake_tool(name):
    def fn(*args, **kwargs):
        return "ok"

    fn.__name__ = name
    return tool(description=f"{name} description")(fn)


class FakeMCPClient:
    def __init__(self, connections, tool_name_prefix=True, handle_tool_errors=True):
        self.connections = connections

    async def get_tools(self, server_name=None):
        return [_fake_tool("demo_add")]


def _fake_factory(connections, tool_name_prefix=True, handle_tool_errors=True):
    return FakeMCPClient(connections, tool_name_prefix, handle_tool_errors)


@pytest.fixture
def mcp_client(client, tmp_path):
    manager = MCPManager(tmp_path / "mcp_servers.json", client_factory=_fake_factory)
    client.app.state.mcp_manager = manager
    yield manager
    manager.unregister_all()


def test_persona_grants_get_put(client, mcp_client):
    client.post(
        "/api/mcp/servers",
        json={"name": "fs", "transport": "stdio", "command": "python"},
    )

    got = client.get("/api/personas/p1/mcp-grants")
    assert got.status_code == 200
    servers = got.json()["servers"]
    free_search = next(item for item in servers if item["name"] == "free-search")
    fs = next(item for item in servers if item["name"] == "fs")
    assert free_search["global"] is True
    assert free_search["authorized"] is True
    assert fs["authorized"] is False

    put = client.put("/api/personas/p1/mcp-grants", json={"server_names": ["fs"]})
    assert put.status_code == 200
    assert put.json()["server_names"] == ["fs"]

    after = client.get("/api/personas/p1/mcp-grants").json()
    assert next(item for item in after["servers"] if item["name"] == "fs")[
        "authorized"
    ] is True

    # 撤销授权
    client.put("/api/personas/p1/mcp-grants", json={"server_names": []})
    after_revoke = client.get("/api/personas/p1/mcp-grants").json()
    assert next(item for item in after_revoke["servers"] if item["name"] == "fs")[
        "authorized"
    ] is False
    # 全局服务器不参与按角色撤销，始终保持可用
    assert next(
        item for item in after_revoke["servers"] if item["name"] == "free-search"
    )["authorized"] is True
