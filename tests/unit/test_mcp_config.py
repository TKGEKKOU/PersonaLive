"""MCP 服务器配置的校验、持久化与连接字典转换。"""

import json

import pytest

from integrations.mcp.config import (
    MCPServerConfig,
    ensure_default_servers,
    load_servers,
    save_servers,
)


def test_config_roundtrip(tmp_path):
    path = tmp_path / "mcp_servers.json"
    servers = [
        MCPServerConfig(
            name="local_fs",
            transport="stdio",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            env={"A": "1"},
            description="本地文件系统",
        ),
        MCPServerConfig(
            name="remote",
            transport="streamable_http",
            url="http://127.0.0.1:8008/mcp",
            headers={"Authorization": "Bearer x"},
            enabled=False,
        ),
    ]
    save_servers(path, servers)
    loaded = load_servers(path)
    assert [s.name for s in loaded] == ["local_fs", "remote"]
    assert loaded[0].command == "npx"
    assert loaded[0].env == {"A": "1"}
    assert loaded[1].transport == "streamable_http"
    assert loaded[1].headers == {"Authorization": "Bearer x"}
    assert loaded[1].enabled is False


def test_load_servers_missing_or_corrupt(tmp_path):
    assert load_servers(tmp_path / "missing.json") == []
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_servers(broken) == []


@pytest.mark.parametrize(
    ("config", "message"),
    [
        (MCPServerConfig(name="Bad Name"), "名称须匹配"),
        (MCPServerConfig(name="ok", transport="carrier-pigeon"), "transport"),
        (MCPServerConfig(name="ok", transport="stdio", command=""), "command"),
        (
            MCPServerConfig(name="ok", transport="streamable_http", url=""),
            "url",
        ),
        (MCPServerConfig(name="ok", transport="sse", url=""), "url"),
    ],
)
def test_validate_rejects_bad_config(config, message):
    with pytest.raises(ValueError, match=message):
        config.validate()


def test_to_connection_shapes():
    stdio = MCPServerConfig(
        name="s", command="python", args=["server.py"], env={"K": "V"}
    )
    assert stdio.to_connection() == {
        "transport": "stdio",
        "command": "python",
        "args": ["server.py"],
        "env": {"K": "V"},
    }
    http = MCPServerConfig(
        name="h", transport="streamable_http", url="http://x/mcp"
    )
    conn = http.to_connection()
    assert conn["transport"] == "streamable_http"
    assert conn["url"] == "http://x/mcp"
    assert conn["headers"] is None


def test_atomic_write_has_no_tmp_leftover(tmp_path):
    path = tmp_path / "mcp_servers.json"
    save_servers(path, [MCPServerConfig(name="s", command="python")])
    assert json.loads(path.read_text(encoding="utf-8"))[0]["name"] == "s"
    assert not list(tmp_path.glob("*.tmp"))


def test_ensure_default_servers_writes_global_free_search(tmp_path):
    path = tmp_path / "mcp_servers.json"
    ensure_default_servers(path)
    servers = load_servers(path)
    assert [s.name for s in servers] == ["free-search"]
    assert servers[0].allowed_persona_ids == ["*"]


def test_ensure_default_servers_migrates_legacy_persona_grants(tmp_path):
    path = tmp_path / "mcp_servers.json"
    save_servers(
        path,
        [
            MCPServerConfig(
                name="free-search",
                command="uvx",
                args=["free-search-mcp"],
                allowed_persona_ids=["p1", "p2"],
            ),
            MCPServerConfig(name="custom", command="python", args=["s.py"]),
        ],
    )
    ensure_default_servers(path)
    servers = load_servers(path)
    free_search = next(s for s in servers if s.name == "free-search")
    custom = next(s for s in servers if s.name == "custom")
    assert free_search.allowed_persona_ids == ["*"]
    assert custom.allowed_persona_ids == []


def test_ensure_default_servers_keeps_explicitly_removed_free_search(tmp_path):
    path = tmp_path / "mcp_servers.json"
    save_servers(
        path,
        [MCPServerConfig(name="custom", command="python", args=["s.py"])],
    )
    ensure_default_servers(path)
    assert [s.name for s in load_servers(path)] == ["custom"]
