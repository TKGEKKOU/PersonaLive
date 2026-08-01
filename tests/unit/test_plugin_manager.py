import asyncio
import json
from pathlib import Path

import pytest

from extensions.events import EventBus
from extensions.manager import PluginManager


def _make_plugin(root: Path, name: str, entry_code: str) -> None:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": name, "version": "0.1.0", "entry": "main.py",
                    "config_schema": {"greeting": {"default": "hi"}}}),
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(entry_code, encoding="utf-8")


def test_load_all_and_lifecycle(tmp_path: Path):
    _make_plugin(tmp_path / "plugins", "greeter",
                 "from extensions.api import PluginContext\n"
                 "EVENTS = []\n"
                 "def on_load(ctx: PluginContext):\n"
                 "    EVENTS.append('load')\n"
                 "    @ctx.on_event('message')\n"
                 "    async def h(event):\n"
                 "        EVENTS.append(event)\n"
                 "def on_unload():\n"
                 "    EVENTS.append('unload')\n")
    bus = EventBus()
    manager = PluginManager(tmp_path / "plugins", tmp_path / "data", bus)
    plugins = manager.load_all()
    assert len(plugins) == 1
    assert plugins[0].name == "greeter"
    assert plugins[0].enabled is False
    assert plugins[0].config == {"greeting": "hi"}

    info = manager.enable("greeter")
    assert info.enabled is True
    asyncio.run(bus.publish("message", "payload"))
    module = manager._plugins["greeter"].module
    assert module.EVENTS == ["load", "payload"]

    assert manager.disable("greeter").enabled is False
    assert module.EVENTS == ["load", "payload", "unload"]


def test_broken_plugin_does_not_break_others(tmp_path: Path):
    _make_plugin(tmp_path / "plugins", "good",
                 "def on_load(ctx):\n    pass\n")
    bad_dir = tmp_path / "plugins" / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "plugin.json").write_text(
        json.dumps({"name": "bad", "version": "0.1.0"}), encoding="utf-8")
    (bad_dir / "main.py").write_text("raise RuntimeError('broken')\n", encoding="utf-8")
    manager = PluginManager(tmp_path / "plugins", tmp_path / "data", EventBus())
    manager.load_all()
    manager.enable("bad")
    plugins = manager.list_plugins()
    by_name = {p.name: p for p in plugins}
    assert by_name["good"].error is None
    assert "broken" in (by_name["bad"].error or "")


def test_state_and_config_persist(tmp_path: Path):
    _make_plugin(tmp_path / "plugins", "greeter",
                 "def on_load(ctx):\n    pass\n")
    manager = PluginManager(tmp_path / "plugins", tmp_path / "data", EventBus())
    manager.load_all()
    manager.enable("greeter")
    manager.save_config("greeter", {"greeting": "hello"})
    state = json.loads((tmp_path / "data" / "plugin_state.json").read_text(encoding="utf-8"))
    config = json.loads((tmp_path / "data" / "plugin_configs.json").read_text(encoding="utf-8"))
    assert state == {"greeter": True}
    assert config == {"greeter": {"greeting": "hello"}}

    manager2 = PluginManager(tmp_path / "plugins", tmp_path / "data", EventBus())
    plugins = manager2.load_all()
    assert plugins[0].enabled is True
    assert plugins[0].config == {"greeting": "hello"}


def test_unknown_plugin_raises(tmp_path: Path):
    manager = PluginManager(tmp_path / "plugins", tmp_path / "data", EventBus())
    with pytest.raises(KeyError):
        manager.enable("missing")
