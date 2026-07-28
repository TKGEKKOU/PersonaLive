import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from anyio import to_thread


ResultT = TypeVar("ResultT")


@dataclass
class _ExecutionEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class ConversationExecutionRegistry:
    """Serialize blocking Agent calls that target the same checkpoint thread."""

    def __init__(self) -> None:
        self._entries: dict[str, _ExecutionEntry] = {}
        self._guard = asyncio.Lock()

    async def run(self, key: str, call: Callable[[], ResultT]) -> ResultT:
        async with self._guard:
            entry = self._entries.setdefault(key, _ExecutionEntry())
            entry.users += 1
        try:
            async with entry.lock:
                return await to_thread.run_sync(call)
        finally:
            async with self._guard:
                entry.users -= 1
                if entry.users == 0 and not entry.lock.locked():
                    self._entries.pop(key, None)
