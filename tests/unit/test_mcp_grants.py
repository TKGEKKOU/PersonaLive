"""MCP 角色授权查询测试：fail-closed 与内置工具不受影响。"""

from integrations.mcp.config import MCPServerConfig, save_servers


def test_grants_fail_closed_and_builtin_visible(tmp_path, monkeypatch):
    from agents import mcp_grants

    path = tmp_path / "mcp_servers.json"
    save_servers(
        path,
        [
            MCPServerConfig(
                name="fs", command="python", args=["s.py"], allowed_persona_ids=["p1"]
            )
        ],
    )
    monkeypatch.setattr(mcp_grants, "_config_path", path)
    mcp_grants.refresh_grants()
    assert mcp_grants.allowed_servers_for_persona("p1") == frozenset({"fs"})
    assert mcp_grants.allowed_servers_for_persona("p2") == frozenset()
    assert mcp_grants.is_mcp_tool_visible("p1", "search_persona_knowledge") is True
    assert mcp_grants.is_mcp_tool_visible("p2", "search_persona_knowledge") is True


def test_grants_global_all_visible_to_every_persona(tmp_path, monkeypatch):
    from agents import mcp_grants

    path = tmp_path / "mcp_servers.json"
    save_servers(
        path,
        [
            MCPServerConfig(
                name="global-fs",
                command="python",
                args=["s.py"],
                allowed_persona_ids=["*"],
            ),
            MCPServerConfig(
                name="per-persona",
                command="python",
                args=["s.py"],
                allowed_persona_ids=["p1"],
            ),
        ],
    )
    monkeypatch.setattr(mcp_grants, "_config_path", path)
    mcp_grants.refresh_grants()
    assert mcp_grants.allowed_servers_for_persona("p1") == frozenset(
        {"global-fs", "per-persona"}
    )
    assert mcp_grants.allowed_servers_for_persona("p2") == frozenset({"global-fs"})
