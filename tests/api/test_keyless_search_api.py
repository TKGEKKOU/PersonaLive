"""免 key 联网搜索开关 API 测试。"""

import pytest

from integrations.mcp.config import MCPServerConfig


class FakeManager:
    def __init__(self):
        self.servers: list = []
        self.status_map: dict = {}
        self.reloaded: list = []
        self.disabled: list = []

    def get_config(self, name):
        return next((s for s in self.servers if s.name == name), None)

    def list_configs(self):
        return list(self.servers)

    def save_configs(self, servers):
        self.servers = list(servers)

    async def reload_server(self, name):
        self.reloaded.append(name)
        self.status_map[name] = {"status": "connected", "tool_count": 2, "error": ""}
        return self.status_map[name]

    def disable_server(self, name):
        self.disabled.append(name)
        self.status_map[name] = {"status": "disabled", "tool_count": 0, "error": ""}

    def status(self):
        return dict(self.status_map)


@pytest.fixture
def keyless(client, monkeypatch):
    manager = FakeManager()
    client.app.state.mcp_manager = manager
    monkeypatch.setattr(
        "app.routers.system.shutil.which",
        lambda name: "C:\\uvx.exe" if name == "uvx" else None,
    )
    return manager


def test_keyless_status_disabled_by_default(client, keyless):
    body = client.get("/api/system/web-search-keyless").json()
    assert body["enabled"] is False
    assert body["uvx_available"] is True


def test_keyless_enable_writes_config_and_reloads(client, keyless):
    response = client.post("/api/system/web-search-keyless", json={"enabled": True})
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["status"] == "connected"
    assert body["tool_count"] == 2
    assert keyless.reloaded == ["free-search"]
    config = keyless.get_config("free-search")
    assert config.command == "uvx"
    assert config.args == [
        "--from",
        "free-search-mcp==0.4.2",
        "--with",
        "mcp==1.29.0",
        "free-search-mcp",
    ]
    assert config.env["UV_DEFAULT_INDEX"] == "https://mirrors.aliyun.com/pypi/simple/"
    assert config.env["SEARCH_MCP_DOWNLOAD_ENABLED"] == "false"
    assert config.allowed_persona_ids == ["*"]


def test_keyless_disable_removes_config(client, keyless):
    client.post("/api/system/web-search-keyless", json={"enabled": True})
    response = client.post("/api/system/web-search-keyless", json={"enabled": False})
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert keyless.disabled == ["free-search"]
    assert keyless.get_config("free-search") is None


def test_keyless_requires_uvx(client, keyless, monkeypatch):
    monkeypatch.setattr("app.routers.system.shutil.which", lambda name: None)
    response = client.post("/api/system/web-search-keyless", json={"enabled": True})
    assert response.status_code == 422
    assert "uvx" in response.json()["detail"]
