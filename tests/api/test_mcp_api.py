"""MCP 服务器管理 API 测试（注入假客户端，不依赖真实 MCP 服务器）。"""

import pytest
from langchain_core.tools import tool

from integrations.mcp.client import MCPManager


def _fake_tool(name, metadata=None):
    def fn(*args, **kwargs):
        return "ok"

    fn.__name__ = name
    built = tool(description=f"{name} description")(fn)
    built.metadata = metadata
    return built


class FakeMCPClient:
    def __init__(self, connections, tool_name_prefix=True, handle_tool_errors=True):
        self.connections = connections

    async def get_tools(self, server_name=None):
        conn = self.connections.get(server_name, {})
        env = conn.get("env") or {}
        if conn.get("fail") or env.get("fail"):
            raise ConnectionError("simulated failure")
        return [_fake_tool("demo_add", metadata={"read_only_hint": True})]


def _fake_factory(connections, tool_name_prefix=True, handle_tool_errors=True):
    return FakeMCPClient(connections, tool_name_prefix, handle_tool_errors)


@pytest.fixture
def mcp_client(client, tmp_path):
    """把真实 MCPManager 替换为注入假客户端的管理器。"""

    manager = MCPManager(tmp_path / "mcp_servers.json", client_factory=_fake_factory)
    client.app.state.mcp_manager = manager
    yield manager
    manager.unregister_all()


def test_mcp_api_preloads_free_search_global(client, mcp_client):
    response = client.get("/api/mcp/servers")
    assert response.status_code == 200
    servers = response.json()
    assert [item["name"] for item in servers] == ["free-search"]
    assert servers[0]["allowed_persona_ids"] == ["*"]
    assert servers[0]["enabled"] is True


def test_mcp_api_create_list_delete(client, mcp_client):
    created = client.post(
        "/api/mcp/servers",
        json={
            "name": "demo",
            "transport": "stdio",
            "command": "python",
            "args": ["server.py"],
            "description": "演示服务器",
        },
    )
    assert created.status_code == 201
    assert created.json()["name"] == "demo"

    listed = client.get("/api/mcp/servers").json()
    assert [item["name"] for item in listed] == ["free-search", "demo"]
    demo = next(item for item in listed if item["name"] == "demo")
    assert demo["status"]["status"] == "connected"

    invalid = client.post(
        "/api/mcp/servers",
        json={"name": "Bad Name", "transport": "stdio", "command": "python"},
    )
    assert invalid.status_code == 422

    missing_command = client.post(
        "/api/mcp/servers",
        json={"name": "no_cmd", "transport": "stdio", "command": ""},
    )
    assert missing_command.status_code == 422

    deleted = client.delete("/api/mcp/servers/demo")
    assert deleted.status_code == 204
    assert [item["name"] for item in client.get("/api/mcp/servers").json()] == [
        "free-search"
    ]

    missing = client.delete("/api/mcp/servers/nope")
    assert missing.status_code == 404


def test_mcp_api_update_preserves_grants_and_masks_secrets(client, mcp_client):
    created = client.post(
        "/api/mcp/servers",
        json={
            "name": "private",
            "transport": "stdio",
            "command": "python",
            "env": {"API_KEY": "secret-value"},
            "headers": {"Authorization": "Bearer secret"},
        },
    )
    assert created.status_code == 201
    grants = client.patch(
        "/api/mcp/servers/private/grants",
        json={"allowed_persona_ids": ["persona-a"]},
    )
    assert grants.status_code == 200

    listed = next(
        item for item in client.get("/api/mcp/servers").json() if item["name"] == "private"
    )
    assert listed["env"] == {"API_KEY": "********"}
    assert listed["headers"] == {"Authorization": "********"}

    updated = client.post(
        "/api/mcp/servers",
        json={
            "name": "private",
            "transport": "stdio",
            "command": "python",
            "description": "updated",
            "env": listed["env"],
            "headers": listed["headers"],
        },
    )
    assert updated.status_code == 201
    saved = mcp_client.get_config("private")
    assert saved.allowed_persona_ids == ["persona-a"]
    assert saved.env == {"API_KEY": "secret-value"}
    assert saved.headers == {"Authorization": "Bearer secret"}


def test_mcp_api_test_connection_ok_and_failed(client, mcp_client):
    client.post(
        "/api/mcp/servers",
        json={"name": "demo", "transport": "stdio", "command": "python"},
    )
    result = client.post("/api/mcp/servers/demo/test")
    assert result.status_code == 200
    body = result.json()
    assert body["ok"] is True
    assert body["tool_count"] == 1
    assert body["tools"][0]["name"] == "demo_add"

    client.post(
        "/api/mcp/servers",
        json={
            "name": "bad",
            "transport": "stdio",
            "command": "python",
            "env": {"fail": "1"},
        },
    )
    failed = client.post("/api/mcp/servers/bad/test")
    assert failed.status_code == 200
    assert failed.json()["ok"] is False
    assert "simulated failure" in failed.json()["error"]


def test_mcp_api_registered_tools(client, mcp_client):
    import asyncio

    from integrations.mcp.config import MCPServerConfig

    mcp_client.save_configs([MCPServerConfig(name="demo", command="python")])
    asyncio.run(mcp_client.connect_all(register=True))
    try:
        tools = client.get("/api/mcp/tools")
        assert tools.status_code == 200
        names = [item["name"] for item in tools.json()]
        assert "demo_add" in names
        assert tools.json()[0]["server"] == "demo"
    finally:
        mcp_client.unregister_all()


def test_mcp_api_enable_disable_reload(client, mcp_client):
    client.post(
        "/api/mcp/servers",
        json={"name": "demo", "transport": "stdio", "command": "python"},
    )
    demo = next(
        item
        for item in client.get("/api/mcp/servers").json()
        if item["name"] == "demo"
    )
    assert demo["status"]["status"] == "connected"

    disabled = client.post("/api/mcp/servers/demo/disable")
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"

    enabled = client.post("/api/mcp/servers/demo/enable")
    assert enabled.status_code == 200
    assert enabled.json()["status"] == "connected"

    reloaded = client.post("/api/mcp/servers/demo/reload")
    assert reloaded.status_code == 200
    assert reloaded.json()["status"] == "connected"

    assert client.post("/api/mcp/servers/nope/enable").status_code == 404
    assert client.post("/api/mcp/servers/nope/disable").status_code == 404
