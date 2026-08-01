import asyncio
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType

from extensions.api import AgentRunner, PluginContext
from extensions.events import EventBus
from extensions.manifest import PluginManifest, PluginManifestError, discover_plugins
from extensions.storage import read_json, write_json


@dataclass(frozen=True)
class PluginInfo:
    name: str
    version: str
    description: str
    author: str
    enabled: bool
    config: dict
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class _PluginRecord:
    manifest: PluginManifest
    module: ModuleType | None = None
    context: PluginContext | None = None
    unsubscribers: list = None
    enabled: bool = False
    error: str | None = None

    def __post_init__(self) -> None:
        self.unsubscribers = []


class PluginManager:
    def __init__(
        self,
        plugins_root: Path,
        data_dir: Path,
        event_bus: EventBus,
        agent_runner: AgentRunner | None = None,
    ) -> None:
        self.plugins_root = plugins_root
        self.data_dir = data_dir
        self.event_bus = event_bus
        self.agent_runner = agent_runner
        self._plugins: dict[str, _PluginRecord] = {}
        self._state_path = data_dir / "plugin_state.json"
        self._config_path = data_dir / "plugin_configs.json"

    def load_all(self) -> list[PluginInfo]:
        self.unload_all()
        state = read_json(self._state_path)
        configs = read_json(self._config_path)
        for plugin_dir in discover_plugins(self.plugins_root):
            try:
                manifest = PluginManifest.load(plugin_dir)
            except PluginManifestError as exc:
                self._plugins[plugin_dir.name] = _PluginRecord(
                    manifest=PluginManifest(plugin_dir.name, "0.0.0"),
                    error=str(exc),
                )
                continue
            record = _PluginRecord(manifest=manifest)
            self._plugins[manifest.name] = record
            if state.get(manifest.name, False):
                self._load_record(manifest.name)
        return self.list_plugins()

    def _load_record(self, name: str) -> None:
        record = self._plugins[name]
        try:
            from extensions.loader import load_plugin_entry

            module = load_plugin_entry(self.plugins_root / name, record.manifest)
            context = PluginContext(
                name=name,
                config={**record.manifest.default_config(), **read_json(self._config_path).get(name, {})},
                config_path=self._config_path,
                event_bus=self.event_bus,
                log=logging.getLogger(f"plugin.{name}"),
                agent_runner=self.agent_runner,
            )
            record.module = module
            record.context = context
            if hasattr(module, "on_load"):
                result = module.on_load(context)
                if asyncio.iscoroutine(result):
                    asyncio.get_event_loop().run_until_complete(result)
            for event, handlers in context.event_handlers().items():
                for handler in handlers:
                    record.unsubscribers.append(self.event_bus.subscribe(event, handler))
            record.enabled = True
            record.error = None
        except Exception as exc:
            record.module = None
            record.context = None
            record.enabled = False
            record.error = str(exc)

    def _unload_record(self, name: str) -> None:
        record = self._plugins.get(name)
        if record is None:
            return
        for unsub in record.unsubscribers:
            unsub()
        record.unsubscribers = []
        if record.module is not None and hasattr(record.module, "on_unload"):
            try:
                result = record.module.on_unload()
                if asyncio.iscoroutine(result):
                    asyncio.get_event_loop().run_until_complete(result)
            except Exception:
                logging.getLogger(__name__).exception("plugin on_unload failed: %s", name)
        record.module = None
        record.context = None
        record.enabled = False

    def list_plugins(self) -> list[PluginInfo]:
        return [self._info(name) for name in sorted(self._plugins)]

    def _info(self, name: str) -> PluginInfo:
        record = self._plugins[name]
        stored_config = read_json(self._config_path).get(name, {})
        return PluginInfo(
            name=record.manifest.name,
            version=record.manifest.version,
            description=record.manifest.description,
            author=record.manifest.author,
            enabled=record.enabled,
            config=record.context.config if record.context else {
                **record.manifest.default_config(),
                **stored_config,
            },
            error=record.error,
        )

    def enable(self, name: str) -> PluginInfo:
        if name not in self._plugins:
            raise KeyError(name)
        record = self._plugins[name]
        if not record.enabled:
            self._load_record(name)
        state = read_json(self._state_path)
        state[name] = True
        write_json(self._state_path, state)
        return self._info(name)

    def disable(self, name: str) -> PluginInfo:
        if name not in self._plugins:
            raise KeyError(name)
        self._unload_record(name)
        state = read_json(self._state_path)
        state[name] = False
        write_json(self._state_path, state)
        return self._info(name)

    def reload(self, name: str) -> PluginInfo:
        if name not in self._plugins:
            raise KeyError(name)
        was_enabled = self._plugins[name].enabled
        self._unload_record(name)
        if was_enabled:
            self._load_record(name)
        return self._info(name)

    def save_config(self, name: str, updates: dict) -> PluginInfo:
        if name not in self._plugins:
            raise KeyError(name)
        configs = read_json(self._config_path)
        current = configs.get(name) or {}
        current.update(updates)
        configs[name] = current
        write_json(self._config_path, configs)
        return self.reload(name)

    def unload_all(self) -> None:
        for name in list(self._plugins):
            self._unload_record(name)
        self._plugins = {}
