import asyncio
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents.context_factory import persona_agent_context
from extensions.events import MessageEvent
from integrations.bindings import (
    bind_persona,
    load_bindings,
    persona_for,
    save_bindings,
)
from integrations.commands import parse_command
from integrations.config import load_integrations, onebot_config
from integrations.qq_official.config import qq_official_config
from persona.service import PersonaNotFound, find_persona_by_name


logger = logging.getLogger(__name__)


class ImMessageRouter:
    def __init__(
        self,
        agent_service,
        session_factory,
        bindings_path: Path,
        integrations_path: Path,
        platform: str = "onebot11",
    ) -> None:
        self.agent_service = agent_service
        self.session_factory = session_factory
        self.bindings_path = bindings_path
        self.integrations_path = integrations_path
        self.platform = platform
        self._locks: dict[str, asyncio.Lock] = {}

    def conversation_id(self, chat_type: str, chat_id: str) -> str:
        return f"im:{self.platform}:{chat_type}:{chat_id}"

    def _config(self) -> dict:
        data = load_integrations(self.integrations_path)
        if self.platform == "qq_official":
            return qq_official_config(data)
        return onebot_config(data)

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def handle(self, event: MessageEvent) -> None:
        if event.platform != self.platform:
            return
        config = self._config()
        if event.chat_type == "group":
            if config["group_trigger"] == "at" and not event.is_at:
                return
            if config["group_trigger"] == "prefix":
                prefix = config.get("prefix") or ""
                if not prefix or not event.content.startswith(prefix):
                    return
                event = replace(event, content=event.content[len(prefix):].strip())
        async with self._lock(event.chat_id):
            command = parse_command(event.content)
            if command is not None:
                await self._handle_command(event, command)
            else:
                await self._handle_question(event)

    async def _handle_command(self, event: MessageEvent, command: tuple[str, str]) -> None:
        kind, argument = command
        if kind == "persona":
            self._bind_persona(event, argument)
        elif kind == "approve":
            await self._resume(event, True)
        elif kind == "reject":
            await self._resume(event, False)
        elif kind == "help":
            event.reply("可用命令：/角色 <名称> 绑定角色；/同意、/拒绝 处理待确认操作；/帮助 查看命令。")

    def _bind_persona(self, event: MessageEvent, name: str) -> None:
        with self.session_factory() as session:
            persona = find_persona_by_name(session, name)
        if persona is None:
            event.reply(f"没有找到名为「{name}」的角色，请到资料页确认角色名称。")
            return
        bindings = load_bindings(self.bindings_path)
        bind_persona(bindings, event.chat_type, event.chat_id, persona.id)
        save_bindings(self.bindings_path, bindings)
        event.reply(f"已绑定角色「{persona.name}」。")

    def _default_persona_id(self) -> str:
        return str(self._config().get("default_persona_id") or "")

    def _persona_for(self, event: MessageEvent) -> str | None:
        bindings = load_bindings(self.bindings_path)
        return persona_for(bindings, event.chat_type, event.chat_id, self._default_persona_id())

    def _context(self, event: MessageEvent) -> Any:
        return persona_agent_context(
            self.session_factory,
            self._persona_for(event),
            self.conversation_id(event.chat_type, event.chat_id),
        )

    async def _handle_question(self, event: MessageEvent) -> None:
        if self._persona_for(event) is None:
            event.reply("还没有绑定角色。请先用 /角色 <名称> 绑定，或在设置页配置默认角色。")
            return
        try:
            context = self._context(event)
        except PersonaNotFound:
            event.reply("绑定的角色不存在了，请重新用 /角色 <名称> 绑定。")
            return
        try:
            result = await asyncio.to_thread(
                self.agent_service.query, event.content, context
            )
        except Exception:
            logger.exception("im agent query failed")
            event.reply("角色暂时无法回复，请稍后再试。")
            return
        if result.status == "pending_confirmation":
            action = result.pending_action or {}
            tool = str(action.get("tool") or "操作")
            event.reply(f"需要确认：{tool}。回复 /同意 或 /拒绝。")
        else:
            event.reply(result.answer or "（空回复）")

    async def _resume(self, event: MessageEvent, approved: bool) -> None:
        if self._persona_for(event) is None:
            event.reply("还没有绑定角色。")
            return
        try:
            context = self._context(event)
            result = await asyncio.to_thread(
                self.agent_service.resume, context, "conversation", approved
            )
        except Exception:
            logger.exception("im agent resume failed")
            event.reply("操作处理失败，请稍后再试。")
            return
        if result.status == "pending_confirmation" or not result.answer:
            event.reply("当前没有待确认的操作。")
        else:
            event.reply(result.answer)
