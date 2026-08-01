import asyncio
import logging
from collections.abc import Callable
from pathlib import Path

from extensions.events import EventBus
from extensions.storage import read_json, write_json

AgentRunner = Callable[[str, str, str], dict]
EventHandler = Callable[..., object]


class PluginContext:
    """插件运行时 API；on_event 收集的 handler 由 PluginManager 注册到 EventBus。"""

    def __init__(
        self,
        name: str,
        config: dict,
        config_path: Path,
        event_bus: EventBus,
        log: logging.Logger | None,
        agent_runner: AgentRunner | None = None,
    ) -> None:
        self._name = name
        self._config = dict(config)
        self._config_path = config_path
        self._event_bus = event_bus
        self.log = log or logging.getLogger(f"plugin.{name}")
        self._agent_runner = agent_runner
        self._handlers: dict[str, list[EventHandler]] = {}

    @property
    def config(self) -> dict:
        return dict(self._config)

    def on_event(self, event: str):
        def decorator(handler: EventHandler) -> EventHandler:
            self._handlers.setdefault(event, []).append(handler)
            return handler

        return decorator

    def handlers_for(self, event: str) -> tuple[EventHandler, ...]:
        return tuple(self._handlers.get(event, ()))

    def event_handlers(self) -> dict[str, list[EventHandler]]:
        return {event: list(handlers) for event, handlers in self._handlers.items()}

    async def save_config(self, updates: dict) -> None:
        self._config.update(updates)
        existing = read_json(self._config_path)
        existing.update({self._name: self._config})
        write_json(self._config_path, existing)

    async def query_agent(self, question: str, persona_id: str, conversation_id: str) -> dict:
        if self._agent_runner is None:
            raise RuntimeError("agent runner 未注入")
        return await asyncio.to_thread(self._agent_runner, question, persona_id, conversation_id)
