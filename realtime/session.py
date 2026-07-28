import asyncio
from collections.abc import Awaitable, Callable
from uuid import uuid4


class TurnInProgressError(RuntimeError):
    pass


class RealtimeSession:
    """Own the active turn for one WebSocket connection."""

    def __init__(self) -> None:
        self._turn_id: str | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def start(
        self,
        factory: Callable[[str], Awaitable[None]],
    ) -> str:
        async with self._lock:
            if self._task and not self._task.done():
                raise TurnInProgressError("The previous turn is still finishing")
            turn_id = str(uuid4())
            self._turn_id = turn_id
            self._task = asyncio.create_task(factory(turn_id))
            return turn_id

    async def cancel(self) -> str | None:
        async with self._lock:
            turn_id = self._turn_id
            self._turn_id = None
        return turn_id

    def is_current(self, turn_id: str) -> bool:
        return self._turn_id == turn_id

    async def finish(self, turn_id: str) -> None:
        async with self._lock:
            if self._turn_id == turn_id:
                self._turn_id = None
            self._task = None

    async def close(self) -> None:
        await self.cancel()
