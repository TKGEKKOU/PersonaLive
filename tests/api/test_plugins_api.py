import json


def _install_fixture_plugin(tmp_path, client):
    plugins_root = tmp_path / "plugins"
    plugin_dir = plugins_root / "greeter"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "greeter", "version": "0.1.0", "entry": "main.py",
                    "config_schema": {"greeting": {"default": "hi"}}}),
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text("def on_load(ctx):\n    pass\n", encoding="utf-8")
    manager = client.app.state.plugin_manager
    manager.plugins_root = plugins_root
    manager.data_dir = tmp_path / "data"
    manager._state_path = manager.data_dir / "plugin_state.json"
    manager._config_path = manager.data_dir / "plugin_configs.json"
    manager.load_all()


def test_plugins_list_and_enable(client, tmp_path):
    _install_fixture_plugin(tmp_path, client)
    listed = client.get("/api/plugins")
    assert listed.status_code == 200
    plugins = listed.json()
    assert plugins[0]["name"] == "greeter"
    assert plugins[0]["version"] == "0.1.0"
    assert plugins[0]["enabled"] is False

    enabled = client.put("/api/plugins/greeter", json={"enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    config = client.put("/api/plugins/greeter/config", json={"config": {"greeting": "hello"}})
    assert config.status_code == 200
    assert config.json()["config"]["greeting"] == "hello"


def test_plugins_unknown_name_returns_404(client):
    response = client.put("/api/plugins/missing", json={"enabled": True})
    assert response.status_code == 404


def test_plugins_api_rejects_extra_fields(client):
    response = client.put("/api/plugins/greeter", json={"enabled": True, "extra": 1})
    assert response.status_code == 422
