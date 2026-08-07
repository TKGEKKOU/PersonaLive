import asyncio
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

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

    async def run_stream(
        self,
        key: str,
        call: Callable[[], Any],
    ) -> AsyncIterator[Any]:
        """在独立线程中运行阻塞生成器，并把事件逐条泵回事件循环。

        保持与 run() 相同的按会话串行语义；线程为 daemon，消费者退出时
        会通知线程停止并在下一轮迭代处结束生成器。
        """
        async with self._guard:
            entry = self._entries.setdefault(key, _ExecutionEntry())
            entry.users += 1
        try:
            async with entry.lock:
                loop = asyncio.get_running_loop()
                queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
                stop = threading.Event()

                def pump(kind: str, payload: Any) -> None:
                    try:
                        loop.call_soon_threadsafe(queue.put_nowait, (kind, payload))
                    except RuntimeError:
                        pass  # 事件循环已关闭，丢弃剩余事件

                def worker() -> None:
                    iterator = call()
                    try:
                        while not stop.is_set():
                            try:
                                item = next(iterator)
                            except StopIteration:
                                break
                            except Exception as exc:
                                pump("error", exc)
                                break
                            pump("item", item)
                    finally:
                        close = getattr(iterator, "close", None)
                        if close is not None:
                            try:
                                close()
                            except Exception:  # pragma: no cover - 关闭失败不影响主流程
                                pass
                        pump("end", None)

                thread = threading.Thread(
                    target=worker,
                    daemon=True,
                    name=f"agent-stream-{key}",
                )
                thread.start()
                try:
                    while True:
                        kind, payload = await queue.get()
                        if kind == "end":
                            break
                        if kind == "error":
                            raise payload
                        yield payload
                finally:
                    stop.set()
                    thread.join(timeout=2)
        finally:
            async with self._guard:
                entry.users -= 1
                if entry.users == 0 and not entry.lock.locked():
                    self._entries.pop(key, None)
