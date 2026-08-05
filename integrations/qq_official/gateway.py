import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from websockets.asyncio.client import connect

from extensions.events import EVENT_MESSAGE, EventBus, MessageEvent
from integrations.qq_official.parser import parse_message_event


logger = logging.getLogger(__name__)

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
WS_URLS = {
    "prod": "wss://api.sgroup.qq.com/websocket/",
    "sandbox": "wss://sandbox.api.sgroup.qq.com/websocket/",
}
API_BASES = {
    "prod": "https://api.sgroup.qq.com",
    "sandbox": "https://sandbox.api.sgroup.qq.com",
}
# 群聊 + 单聊消息（含 @ 机器人和全量群消息）
INTENT_GROUP_AND_C2C = 1 << 25

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

MESSAGE_EVENTS = frozenset(
    {"GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE"}
)


class QqOfficialGateway:
    """QQ 官方机器人 WebSocket 网关客户端。

    - 通过 appid/secret 换取 access_token（缓存至接近过期）；
    - 连接官方网关，Identify/Resume 鉴权并维持心跳，断线自动重连；
    - 收到群聊/单聊消息后经 EventBus 广播为 MessageEvent；
    - 提供 send_message 主动推送。
    """

    def __init__(self, config_provider: Callable[[], dict], event_bus: EventBus) -> None:
        self._config_provider = config_provider
        self._event_bus = event_bus
        self._token = ""
        self._token_expires_at = 0.0
        self._session_id: str | None = None
        self._last_seq: int | None = None
        self._bot_openid: str | None = None
        self._heartbeat_interval = 30000
        self._connected = False
        self._error: str | None = None
        self._task: asyncio.Task | None = None
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._http: httpx.AsyncClient | None = None
        self._pending: set[asyncio.Task] = set()
        self._recent_msg_ids: set[str] = set()

    # ---------- 生命周期 ----------
    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def config_changed(self) -> None:
        """配置保存后立即唤醒主循环，让启用/禁用/参数变更尽快生效。"""
        self._wake.set()

    def config(self) -> dict:
        return self._config_provider()

    def status(self) -> dict:
        cfg = self.config()
        return {
            "enabled": bool(cfg.get("enabled")),
            "configured": bool(cfg.get("appid")) and bool(cfg.get("secret")),
            "sandbox": bool(cfg.get("sandbox")),
            "connected": bool(cfg.get("enabled")) and self._connected,
            "error": self._error,
            "bot_openid": self._bot_openid,
        }

    # ---------- 主循环：禁用时等待，启用时连接，断线指数退避重连 ----------
    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            cfg = self.config()
            if not cfg.get("enabled") or not cfg.get("appid") or not cfg.get("secret"):
                self._connected = False
                self._error = None
                self._session_id = None
                self._last_seq = None
                await self._sleep_or_wake(2.0)
                continue
            try:
                await self._connect_once(cfg)
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._connected = False
                self._error = str(exc)
                logger.exception("QQ official gateway connection failed")
            await self._sleep_or_wake(min(backoff, 30.0))
            backoff = min(backoff * 2.0, 30.0)

    async def _sleep_or_wake(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    # ---------- WebSocket 连接 ----------
    async def _connect_once(self, cfg: dict) -> None:
        token = await self._ensure_token(cfg)
        mode = "sandbox" if cfg.get("sandbox") else "prod"
        self._error = None
        try:
            async with connect(
                WS_URLS[mode],
                additional_headers={"Authorization": f"QQBot {token}"},
                max_size=16 * 1024 * 1024,
                open_timeout=15,
            ) as ws:
                await self._handshake(ws, token, cfg)
                self._connected = True
                heartbeat_task = asyncio.create_task(self._heartbeat_loop(ws))
                try:
                    async for raw in ws:
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        await self._handle_payload(payload)
                finally:
                    heartbeat_task.cancel()
        finally:
            self._connected = False

    async def _handshake(self, ws, token: str, cfg: dict) -> None:
        hello = json.loads(await ws.recv())
        if hello.get("op") != OP_HELLO:
            raise ConnectionError("QQ official gateway did not send Hello")
        self._heartbeat_interval = int(((hello.get("d") or {}).get("heartbeat_interval") or 30000))
        # 有会话则先尝试 Resume 补发断线期间事件；失败再重新 Identify
        if self._session_id and self._last_seq is not None:
            await ws.send(
                json.dumps(
                    {
                        "op": OP_RESUME,
                        "d": {
                            "token": f"QQBot {token}",
                            "session_id": self._session_id,
                            "seq": self._last_seq,
                        },
                    }
                )
            )
            resumed = await self._await_ready(ws)
            if not resumed:
                self._session_id = None
                self._last_seq = None
                await self._send_identify(ws, token)
                await self._await_ready(ws)
        else:
            await self._send_identify(ws, token)
            await self._await_ready(ws)

    async def _send_identify(self, ws, token: str) -> None:
        await ws.send(
            json.dumps(
                {
                    "op": OP_IDENTIFY,
                    "d": {
                        "token": f"QQBot {token}",
                        "intents": INTENT_GROUP_AND_C2C,
                        "shard": [0, 1],
                        "properties": {"$os": "yumeno", "$browser": "yumeno", "$device": "yumeno"},
                    },
                }
            )
        )

    async def _await_ready(self, ws) -> bool:
        """等待 READY/RESUMED；INVALID_SESSION 返回 False。"""
        while True:
            try:
                payload = json.loads(await ws.recv())
            except json.JSONDecodeError:
                continue
            op = payload.get("op")
            if op == OP_DISPATCH:
                t = payload.get("t")
                d = payload.get("d") or {}
                self._last_seq = payload.get("s") or self._last_seq
                if t == "READY":
                    user = d.get("user") or {}
                    self._bot_openid = str(user.get("id") or "") or None
                    self._session_id = str(d.get("session_id") or "") or None
                    return True
                if t == "RESUMED":
                    return True
            elif op == OP_INVALID_SESSION:
                return False
            elif op == OP_RECONNECT:
                raise ConnectionError("gateway requested reconnect during handshake")

    async def _heartbeat_loop(self, ws) -> None:
        while True:
            await asyncio.sleep(max(self._heartbeat_interval / 1000, 1.0))
            await ws.send(json.dumps({"op": OP_HEARTBEAT, "d": self._last_seq}))

    async def _handle_payload(self, payload: dict) -> None:
        op = payload.get("op")
        if op == OP_DISPATCH:
            self._last_seq = payload.get("s") or self._last_seq
            event_name = payload.get("t")
            if event_name in MESSAGE_EVENTS:
                data = payload.get("d") or {}
                task = asyncio.create_task(self._process_message(event_name, data))
                self._pending.add(task)
                task.add_done_callback(self._pending.discard)
        elif op == OP_RECONNECT:
            raise ConnectionError("gateway requested reconnect")
        elif op == OP_INVALID_SESSION:
            self._session_id = None
            self._last_seq = None
            raise ConnectionError("gateway invalidated session")

    # ---------- 消息处理 ----------
    async def _process_message(self, event_name: str, data: dict) -> None:
        message = parse_message_event(event_name, data, self._bot_openid)
        if message is None or not message.msg_id:
            return
        if not message.text.strip():
            # 图片/语音/卡片等非文本消息暂不进入 Agent 对话
            return
        # 官方网关可能重复推送同一 msg_id，做窗口去重
        if message.msg_id in self._recent_msg_ids:
            return
        self._recent_msg_ids.add(message.msg_id)
        if len(self._recent_msg_ids) > 500:
            self._recent_msg_ids = set(list(self._recent_msg_ids)[-300:])

        def reply(text: str) -> None:
            task = asyncio.create_task(
                self.send_message(message.chat_type, message.chat_id, text, message.msg_id)
            )
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)

        event = MessageEvent(
            platform="qq_official",
            chat_type=message.message_type,
            chat_id=message.chat_id,
            user_id=message.user_id,
            content=message.text.strip(),
            raw_content=message.raw_content,
            reply=reply,
            is_at=message.is_at,
        )
        await self._event_bus.publish(EVENT_MESSAGE, event)

    # ---------- 凭证与主动推送 ----------
    async def _ensure_token(self, cfg: dict) -> str:
        if self._token and self._token_expires_at - time.monotonic() > 60:
            return self._token
        client = self._http_client()
        resp = await client.post(
            TOKEN_URL, json={"appId": cfg["appid"], "clientSecret": cfg["secret"]}
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"QQ official token error: {resp.status_code} {resp.text[:200]}")
        data = resp.json()
        self._token = str(data.get("access_token") or "")
        if not self._token:
            raise RuntimeError("QQ official token response missing access_token")
        self._token_expires_at = time.monotonic() + int(float(data.get("expires_in", 7200))) - 30
        return self._token

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=20)
        return self._http

    async def send_message(
        self,
        chat_type: str,
        chat_id: str,
        content: str,
        msg_id: str | None = None,
    ) -> bool:
        """发送文本消息（群聊或单聊）。带 msg_id 时按被动回复处理，可减少审核。"""
        cfg = self.config()
        if not cfg.get("enabled"):
            return False
        try:
            token = await self._ensure_token(cfg)
        except Exception:
            logger.exception("QQ official send: token refresh failed")
            return False
        mode = "sandbox" if cfg.get("sandbox") else "prod"
        if chat_type == "group":
            url = f"{API_BASES[mode]}/v2/groups/{chat_id}/messages"
        elif chat_type == "c2c":
            url = f"{API_BASES[mode]}/v2/users/{chat_id}/messages"
        else:
            return False
        body: dict[str, Any] = {"msg_type": 0, "content": content}
        if msg_id:
            body["msg_id"] = msg_id
        try:
            resp = await self._http_client().post(
                url, headers={"Authorization": f"QQBot {token}"}, json=body
            )
        except Exception:
            logger.exception("QQ official send failed")
            return False
        if resp.status_code >= 400:
            if msg_id:
                # 被动回复超时/越权时，去掉 msg_id 按主动消息再试一次
                logger.warning(
                    "QQ official reply rejected (%s), retrying without msg_id: %s",
                    resp.status_code, resp.text[:200],
                )
                return await self.send_message(chat_type, chat_id, content)
            logger.warning("QQ official send rejected: %s %s", resp.status_code, resp.text[:300])
            return False
        return True
