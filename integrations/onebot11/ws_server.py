import asyncio
import logging
from collections.abc import Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from extensions.events import EVENT_MESSAGE, EventBus, MessageEvent
from integrations.onebot11.parser import parse_message_event


logger = logging.getLogger(__name__)
router = APIRouter()


class OneBotConnectionManager:
    def __init__(self, config_provider: Callable[[], dict]) -> None:
        self._config_provider = config_provider
        self._connections: list[WebSocket] = []
        self._tasks: set[asyncio.Task] = set()
        self._error: str | None = None

    def config(self) -> dict:
        return self._config_provider()

    def config_changed(self, config: dict) -> None:
        if not config.get("enabled"):
            for websocket in list(self._connections):
                self._spawn(websocket.close(code=1008, reason="integration disabled"))

    def status(self) -> dict:
        config = self.config()
        return {
            "connected": bool(config.get("enabled")) and bool(self._connections),
            "client_count": len(self._connections),
            "error": self._error,
        }

    def send_action(self, action: str, params: dict) -> None:
        payload = {"action": action, "params": params, "echo": ""}
        for websocket in list(self._connections):
            self._spawn(websocket.send_json(payload))

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _token_ok(self, websocket: WebSocket) -> bool:
        token = str(self.config().get("access_token") or "")
        if not token:
            return True
        header = websocket.headers.get("authorization") or ""
        if header == f"Bearer {token}":
            return True
        return websocket.query_params.get("access_token") == token

    async def handle_connection(self, websocket: WebSocket, event_bus: EventBus) -> None:
        if not self.config().get("enabled"):
            await websocket.close(code=1008, reason="integration disabled")
            return
        if not self._token_ok(websocket):
            await websocket.close(code=1008, reason="invalid access token")
            return
        await websocket.accept()
        self._connections.append(websocket)
        self._error = None
        try:
            while True:
                payload = await websocket.receive_json()
                await self._publish_event(payload, event_bus)
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            self._error = str(exc)
            logger.exception("onebot websocket error")
        finally:
            if websocket in self._connections:
                self._connections.remove(websocket)

    async def _publish_event(self, payload: dict, event_bus: EventBus) -> None:
        message = parse_message_event(payload)
        if message is None:
            return

        def reply(text: str) -> None:
            if message.message_type == "group":
                self.send_action(
                    "send_group_msg",
                    {"group_id": int(message.group_id), "message": text},
                )
            else:
                self.send_action(
                    "send_private_msg",
                    {"user_id": int(message.user_id), "message": text},
                )

        event = MessageEvent(
            platform="onebot11",
            chat_type=message.message_type,
            chat_id=message.group_id or message.user_id,
            user_id=message.user_id,
            content=message.text.strip(),
            raw_content=message.text,
            reply=reply,
            is_at=message.is_at,
        )
        await event_bus.publish(EVENT_MESSAGE, event)


@router.websocket("/api/onebot/ws")
async def onebot_ws(websocket: WebSocket) -> None:
    manager = websocket.app.state.onebot
    await manager.handle_connection(websocket, websocket.app.state.event_bus)
