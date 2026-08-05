"""MCP 客户端管理器。

基于 langchain-mcp-adapters 的 ``MultiServerMCPClient`` 连接外部 MCP
服务器。0.3.x 版本起客户端采用无状态模式：``get_tools()`` 时创建会话
拉取工具清单，工具被调用时再各自创建一次性会话，因此管理器不需要
持有长连接，也无需 ``aclose()``。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient

from agents.registry import ToolSpec, register_tool_specs, tool_specs
from integrations.mcp.config import (
    MCPServerConfig,
    ensure_default_servers,
    load_servers,
    save_servers,
)


logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 20
MCP_TOOL_TIMEOUT_SECONDS = 60


_FREE_SEARCH_ENGINE_HINT = (
    "\n\n【引擎提示】本服务器默认已启用国内可直接访问的百度引擎。调用 search / research 时"
    "不要手动传 engines 参数——尤其不要传 duckduckgo、mojeek、googlenews、bing、startpage、"
    "brave、google、searx 等引擎，它们在国内网络下会长时间超时并返回空结果。"
    '只有需要搜索 B 站视频时才传 engines=["bilibili"]。'
)


def _patch_free_search_description(name: str, description: str, server: str = "") -> str:
    """修正 free-search 服务端工具描述中的引擎误导。"""

    if server == "free-search" and name in {"search", "research"}:
        return description + _FREE_SEARCH_ENGINE_HINT
    return description


def _friendly_error(exc: Exception, limit: int = 300) -> str:
    """提取 MCP 连接失败的真实原因。

    langchain-mcp-adapters 用 anyio TaskGroup 聚合子进程错误，直接 ``str()``
    只会得到 "unhandled errors in a TaskGroup"，真实异常被包在
    ``BaseExceptionGroup`` 里。这里递归取第一个子异常，保留可读的错误消息。
    """

    if isinstance(exc, BaseExceptionGroup):
        if exc.exceptions:
            return _friendly_error(exc.exceptions[0], limit)
        return str(exc)[:limit]
    message = str(exc).strip()
    return message[:limit] if message else type(exc).__name__


@dataclass(frozen=True)
class MCPToolInfo:
    """注册进 ToolSpec 前的 MCP 工具元数据（前端展示与安全分类共用）。"""

    name: str
    description: str
    server: str
    requires_confirmation: bool
    mutates_data: bool


def classify_mcp_tool(tool: BaseTool) -> tuple[bool, bool]:
    """根据 MCP 工具注解判断 (requires_confirmation, mutates_data)。

    规则：
    - ``read_only_hint=True`` -> 只读，不要求确认；
    - 明确声明非只读（read_only_hint=False）或 ``destructive_hint=True``
      -> 视为写操作，要求 HITL 确认；
    - 服务器未声明任何注解 -> 默认按只读处理，方便日常使用。
    """

    metadata = tool.metadata if isinstance(tool.metadata, dict) else {}
    read_only = bool(
        metadata.get("read_only_hint", metadata.get("readOnlyHint"))
    )
    destructive = bool(
        metadata.get("destructive_hint", metadata.get("destructiveHint"))
    )
    declared = "read_only_hint" in metadata or "readOnlyHint" in metadata
    if read_only:
        return False, False
    if declared or destructive:
        return True, True
    return False, False


def _tool_info(name: str, server: str, tool: BaseTool) -> MCPToolInfo:
    requires_confirmation, mutates_data = classify_mcp_tool(tool)
    return MCPToolInfo(
        name=name,
        description=_patch_free_search_description(
            name, str(tool.description or ""), server
        ),
        server=server,
        requires_confirmation=requires_confirmation,
        mutates_data=mutates_data,
    )


def _make_sync_tool(original: BaseTool, server_name: str = "") -> BaseTool:
    """把仅支持异步调用的 MCP 工具包装为可在同步 Agent 链路中调用。

    YUMENO 的 workflow 是同步 invoke，并在工作线程（anyio.to_thread /
    FastAPI 同步端点）中执行，线程内没有运行中的事件循环，因此可以安全地
    用 asyncio.run 桥接异步工具；工具 schema 与 metadata 保持原样。
    """

    from langchain_core.tools import tool as make_tool

    async def _run(args: dict):
        # 单次工具调用兜底超时：搜索/抓取可能很慢或子进程挂起，
        # 超时后返回错误让模型给出说明，而不是让整轮对话无限等待。
        return await asyncio.wait_for(
            original.ainvoke(args), timeout=MCP_TOOL_TIMEOUT_SECONDS
        )

    @make_tool(
        original.name,
        description=_patch_free_search_description(
            original.name, original.description or "", server_name
        ),
    )
    def sync_tool(**kwargs):
        return asyncio.run(_run(kwargs))

    sync_tool.args_schema = original.args_schema
    sync_tool.metadata = getattr(original, "metadata", None)
    return sync_tool


class MCPManager:
    """管理 MCP 服务器配置、连接状态与工具注册。

    生命周期：应用启动时 ``connect_all()`` 连接所有启用的服务器并注册
    工具；配置变更后需重启应用生效（与插件机制一致）。``client_factory``
    可注入，便于测试时替换为假客户端。
    """

    def __init__(
        self,
        config_path: Path,
        client_factory: Callable[..., MultiServerMCPClient] | None = None,
        allow_arbitrary_stdio: bool = False,
    ) -> None:
        self.config_path = Path(config_path)
        self._client_factory = client_factory or MultiServerMCPClient
        self._allow_arbitrary = allow_arbitrary_stdio
        ensure_default_servers(self.config_path)
        self._status: dict[str, dict] = {}
        self._registered: list[MCPToolInfo] = []

    # ---- 配置 ----

    def list_configs(self) -> list[MCPServerConfig]:
        return load_servers(self.config_path)

    def save_configs(self, servers: list[MCPServerConfig]) -> None:
        for server in servers:
            server.validate(allow_arbitrary_stdio=self._allow_arbitrary)
        names = [server.name for server in servers]
        if len(names) != len(set(names)):
            raise ValueError("服务器名称不能重复")
        save_servers(self.config_path, servers)

    def get_config(self, name: str) -> MCPServerConfig | None:
        return next((s for s in self.list_configs() if s.name == name), None)

    # ---- 连接与注册 ----

    async def _fetch_tools(
        self, config: MCPServerConfig
    ) -> list[tuple[BaseTool, MCPToolInfo]]:
        # 不用服务器名前缀：工具名保持 MCP 服务器原生名（如 search / research），
        # 标准 SKILL.md 技能包的 tool-names 才能直接引用；多服务器同名工具由
        # 注册时的名称冲突跳过兜底。
        client = self._client_factory(
            {config.name: config.to_connection()},
            tool_name_prefix=False,
            handle_tool_errors=True,
        )
        tools = await client.get_tools(server_name=config.name)
        return [
            (tool, _tool_info(tool.name, config.name, tool))
            for tool in tools
        ]

    async def connect_server(
        self, config: MCPServerConfig
    ) -> list[MCPToolInfo]:
        """连接单个服务器并返回工具信息；失败时抛出异常。"""

        entries = await asyncio.wait_for(
            self._fetch_tools(config), timeout=CONNECT_TIMEOUT_SECONDS
        )
        return [info for _, info in entries]

    async def connect_all(self, register: bool = True) -> dict[str, dict]:
        """连接所有启用的服务器；单个失败仅记录错误，不阻塞其他服务器。

        register=True 时把成功连接的工具注册进 ToolSpec 表（内部经
        ``enable_server``，含安全校验）。返回每个服务器的连接状态，供前端展示。
        """

        self._status = {}
        for config in self.list_configs():
            if not config.enabled:
                self._mark(config.name, "disabled", 0)
                continue
            try:
                if register:
                    await self.enable_server(config)
                else:
                    entries = await asyncio.wait_for(
                        self._fetch_tools(config),
                        timeout=CONNECT_TIMEOUT_SECONDS,
                    )
                    self._mark(config.name, "connected", len(entries))
            except Exception as exc:
                logger.warning(
                    "MCP 服务器 %s 连接失败: %s",
                    config.name,
                    _friendly_error(exc),
                    exc_info=True,
                )
                self._mark(config.name, "error", 0, _friendly_error(exc))
        return dict(self._status)

    def _mark(self, name: str, status: str, tool_count: int, error: str = "") -> dict:
        """记录服务器状态并返回该条目（含 last_check 时间戳）。"""

        entry = {
            "status": status,
            "tool_count": tool_count,
            "error": error,
            "last_check": datetime.now().isoformat(timespec="seconds"),
        }
        self._status[name] = entry
        return entry

    def _register_server(
        self,
        server_name: str,
        entries: list[tuple[BaseTool, MCPToolInfo]],
    ) -> None:
        """把单个服务器的工具包装成 ToolSpec 注册进 registry（名称冲突跳过）。"""

        specs: list[ToolSpec] = []
        known = {spec.name for spec in tool_specs()}
        for tool, info in entries:
            if info.name in known:
                logger.warning("跳过 MCP 工具 %s：名称与现有工具冲突", info.name)
                continue
            specs.append(
                ToolSpec(
                    name=info.name,
                    specialist="mcp",
                    tool=_make_sync_tool(tool, server_name),
                    requires_confirmation=info.requires_confirmation,
                    mutates_data=info.mutates_data,
                    server=server_name,
                )
            )
            known.add(info.name)
            self._registered.append(info)
        register_tool_specs(specs)
        try:
            from agents.skills import refresh_skills

            refresh_skills()
        except Exception:
            pass

    def _unregister_server(self, name: str) -> None:
        """注销指定服务器注册的工具；不影响其他服务器。"""

        from agents.registry import unregister_tool_specs

        names = [info.name for info in self._registered if info.server == name]
        if names:
            unregister_tool_specs(names)
        self._registered = [info for info in self._registered if info.server != name]

    async def enable_server(self, config: MCPServerConfig) -> dict:
        """启用并连接单个服务器：安全校验 → 连接 → 注册工具。"""

        config.validate(allow_arbitrary_stdio=self._allow_arbitrary)
        entries = await asyncio.wait_for(
            self._fetch_tools(config), timeout=CONNECT_TIMEOUT_SECONDS
        )
        self._register_server(config.name, entries)
        return self._mark(config.name, "connected", len(entries))

    def disable_server(self, name: str) -> dict:
        """停用单个服务器：注销其工具并标记 disabled。"""

        self._unregister_server(name)
        return self._mark(name, "disabled", 0)

    async def reload_server(self, name: str) -> dict:
        """配置变更后重连单个服务器；失败时标记 error 并保留原因。"""

        config = self.get_config(name)
        if config is None:
            raise KeyError(f"Unknown MCP server: {name}")
        self._unregister_server(name)
        if not config.enabled:
            return self._mark(name, "disabled", 0)
        try:
            return await self.enable_server(config)
        except Exception as exc:
            logger.warning(
                "MCP 服务器 %s 重连失败: %s",
                name,
                _friendly_error(exc),
                exc_info=True,
            )
            return self._mark(name, "error", 0, _friendly_error(exc))

    def registered_tools(self) -> list[MCPToolInfo]:
        return list(self._registered)

    def status(self) -> dict[str, dict]:
        return dict(self._status)

    def unregister_all(self) -> None:
        """清空已注册的 MCP 工具（供配置变更后重建时使用）。"""

        from agents.registry import unregister_tool_specs

        names = [info.name for info in self._registered]
        unregister_tool_specs(names)
        self._registered = []
