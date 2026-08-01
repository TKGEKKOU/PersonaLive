import json
from pathlib import Path

import pytest

from extensions.manifest import PluginManifest, PluginManifestError, discover_plugins


def _write_plugin(root: Path, name: str, data: dict) -> Path:
    plugin_dir = root / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
    (plugin_dir / "main.py").write_text("", encoding="utf-8")
    return plugin_dir


def test_load_valid_manifest(tmp_path: Path):
    plugin_dir = _write_plugin(tmp_path, "my_plugin", {
        "name": "my_plugin",
        "version": "0.1.0",
        "description": "测试插件",
        "author": "tester",
        "entry": "main.py",
        "config_schema": {"greeting": {"type": "string", "default": "hi"}},
    })
    manifest = PluginManifest.load(plugin_dir)
    assert manifest.name == "my_plugin"
    assert manifest.version == "0.1.0"
    assert manifest.description == "测试插件"
    assert manifest.author == "tester"
    assert manifest.entry == "main.py"
    assert manifest.config_schema["greeting"]["default"] == "hi"
    assert manifest.default_config() == {"greeting": "hi"}


def test_load_uses_defaults(tmp_path: Path):
    plugin_dir = _write_plugin(tmp_path, "minimal", {"name": "minimal", "version": "1.0.0"})
    manifest = PluginManifest.load(plugin_dir)
    assert manifest.entry == "main.py"
    assert manifest.config_schema == {}
    assert manifest.description == ""
    assert manifest.author == ""


@pytest.mark.parametrize("bad", [
    {"version": "0.1.0"},
    {"name": "minimal"},
    {"name": "Bad Name", "version": "0.1.0"},
    {"name": "other_dir", "version": "0.1.0"},
    {"name": "minimal", "version": ""},
    {"name": "minimal", "version": "0.1.0", "entry": "missing.py"},
])
def test_invalid_manifests_raise(tmp_path: Path, bad: dict):
    plugin_dir = _write_plugin(tmp_path, "minimal", bad)
    with pytest.raises(PluginManifestError):
        PluginManifest.load(plugin_dir)


def test_load_rejects_corrupt_json(tmp_path: Path):
    plugin_dir = tmp_path / "broken"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PluginManifestError):
        PluginManifest.load(plugin_dir)


def test_discover_plugins_lists_only_dirs_with_manifest(tmp_path: Path):
    _write_plugin(tmp_path, "aaa", {"name": "aaa", "version": "0.1.0"})
    _write_plugin(tmp_path, "bbb", {"name": "bbb", "version": "0.1.0"})
    (tmp_path / "empty").mkdir()
    assert [p.name for p in discover_plugins(tmp_path)] == ["aaa", "bbb"]
