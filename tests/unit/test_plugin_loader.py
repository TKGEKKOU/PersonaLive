import asyncio
import json
from pathlib import Path

from extensions.api import PluginContext
from extensions.events import EventBus
from extensions.loader import load_plugin_entry
from extensions.manifest import PluginManifest
from extensions.storage import read_json, write_json


def test_write_and_read_json_roundtrip(tmp_path: Path):
    target = tmp_path / "data" / "test.json"
    write_json(target, {"a": 1, "nested": {"b": True}})
    assert read_json(target) == {"a": 1, "nested": {"b": True}}


def test_read_json_missing_file_returns_empty(tmp_path: Path):
    assert read_json(tmp_path / "missing.json") == {}


def test_load_plugin_entry_imports_module(tmp_path: Path):
    plugin_dir = tmp_path / "greeter"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(
        json.dumps({"name": "greeter", "version": "0.1.0", "entry": "main.py"}),
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(
        "GREETING = '你好'\n\ndef on_load(ctx):\n    ctx.log.info('loaded')\n",
        encoding="utf-8",
    )
    manifest = PluginManifest.load(plugin_dir)
    module = load_plugin_entry(plugin_dir, manifest)
    assert module.GREETING == "你好"
    assert callable(module.on_load)


def test_plugin_context_events_and_agent_runner(tmp_path: Path):
    bus = EventBus()
    calls = []

    def runner(question, persona_id, conversation_id):
        calls.append((question, persona_id, conversation_id))
        return {"status": "completed", "answer": "ok"}

    ctx = PluginContext(
        name="greeter",
        config={"greeting": "hi"},
        config_path=tmp_path / "config.json",
        event_bus=bus,
        log=None,
        agent_runner=runner,
    )

    @ctx.on_event("message")
    async def handler(event):
        calls.append(event)

    assert len(ctx.handlers_for("message")) == 1
    result = asyncio.run(ctx.query_agent("问题", "p1", "c1"))
    assert result["answer"] == "ok"
    assert calls[0] == ("问题", "p1", "c1")


def test_plugin_context_save_config_persists(tmp_path: Path):
    config_path = tmp_path / "config.json"
    ctx = PluginContext(
        name="greeter",
        config={"greeting": "hi"},
        config_path=config_path,
        event_bus=EventBus(),
        log=None,
        agent_runner=None,
    )
    asyncio.run(ctx.save_config({"greeting": "hello"}))
    assert json.loads(config_path.read_text(encoding="utf-8")) == {"greeter": {"greeting": "hello"}}
    assert ctx.config == {"greeting": "hello"}
