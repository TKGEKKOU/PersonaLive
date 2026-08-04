import importlib.util
from pathlib import Path
from types import ModuleType

from extensions.manifest import PluginManifest


def load_plugin_entry(plugin_dir: Path, manifest: PluginManifest) -> ModuleType:
    entry_path = plugin_dir / manifest.entry
    module_name = f"yumeno_plugin_{manifest.name}"
    spec = importlib.util.spec_from_file_location(module_name, entry_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载插件入口 {entry_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
