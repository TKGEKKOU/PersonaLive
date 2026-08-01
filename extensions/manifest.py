import json
import re
from dataclasses import dataclass, field
from pathlib import Path


class PluginManifestError(ValueError):
    pass


@dataclass(frozen=True)
class PluginManifest:
    name: str
    version: str
    description: str = ""
    author: str = ""
    entry: str = "main.py"
    config_schema: dict = field(default_factory=dict)

    @classmethod
    def load(cls, plugin_dir: Path) -> "PluginManifest":
        manifest_path = plugin_dir / "plugin.json"
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PluginManifestError(f"{manifest_path} 无法读取或不是合法 JSON") from exc
        if not isinstance(data, dict):
            raise PluginManifestError(f"{manifest_path} 顶层必须是 JSON 对象")

        name = str(data.get("name") or "").strip()
        if not re.fullmatch(r"[a-z0-9_-]+", name):
            raise PluginManifestError("name 必须匹配 [a-z0-9_-]+")
        if name != plugin_dir.name:
            raise PluginManifestError("name 必须与插件目录名一致")

        version = str(data.get("version") or "").strip()
        if not version:
            raise PluginManifestError("version 不能为空")

        entry = str(data.get("entry") or "main.py").strip()
        if not entry or not (plugin_dir / entry).is_file():
            raise PluginManifestError(f"入口文件 {entry} 不存在")

        config_schema = data.get("config_schema") or {}
        if not isinstance(config_schema, dict):
            raise PluginManifestError("config_schema 必须是对象")
        return cls(
            name=name,
            version=version,
            description=str(data.get("description") or ""),
            author=str(data.get("author") or ""),
            entry=entry,
            config_schema=config_schema,
        )

    def default_config(self) -> dict:
        return {
            key: (value.get("default") if isinstance(value, dict) else None)
            for key, value in self.config_schema.items()
        }


def discover_plugins(plugins_root: Path) -> list[Path]:
    if not plugins_root.is_dir():
        return []
    return sorted(
        (child for child in plugins_root.iterdir() if (child / "plugin.json").is_file()),
        key=lambda path: path.name,
    )
