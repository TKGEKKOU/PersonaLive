from integrations.mcp.config import MCPServerConfig


def test_grants_patch_updates_config_and_grants(client, tmp_path, monkeypatch):
    import agents.mcp_grants as grants_module

    manager = client.app.state.mcp_manager
    monkeypatch.setattr(manager, "config_path", tmp_path / "mcp_servers.json")
    monkeypatch.setattr(grants_module, "_config_path", tmp_path / "mcp_servers.json")
    manager.save_configs(
        [
            MCPServerConfig(
                name="filesystem",
                transport="stdio",
                command="python",
                args=["-m", "demo_mcp", "D:/data"],
                allowed_persona_ids=["*"],
            )
        ]
    )
    grants_module.refresh_grants()

    response = client.patch(
        "/api/mcp/servers/filesystem/grants",
        json={"allowed_persona_ids": ["persona-a"]},
        headers={"X-YUMENO-Request": "web"},
    )
    assert response.status_code == 200
    assert response.json()["allowed_persona_ids"] == ["persona-a"]
    assert grants_module.allowed_servers_for_persona("persona-a") == {"filesystem"}
    assert grants_module.allowed_servers_for_persona("persona-b") == set()
