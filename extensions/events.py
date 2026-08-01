import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

Handler = Callable[[Any], Any]

EVENT_MESSAGE = "message"


@dataclass(frozen=True)
class MessageEvent:
    platform: str
    chat_type: str
    chat_id: str
    user_id: str
    content: str
    raw_content: str
    reply: Callable[[str], None]
    is_at: bool = False


class EventBus:
    """进程内事件分发器；单个 handler 异常不影响其他订阅者。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = {}

    def subscribe(self, event: str, handler: Handler) -> Callable[[], None]:
        self._handlers.setdefault(event, []).append(handler)

        def unsubscribe() -> None:
            handlers = self._handlers.get(event)
            if handlers and handler in handlers:
                handlers.remove(handler)

        return unsubscribe

    async def publish(self, event: str, payload: Any) -> None:
        for handler in list(self._handlers.get(event, ())):
            try:
                result = handler(payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("event handler failed for event=%s", event)
