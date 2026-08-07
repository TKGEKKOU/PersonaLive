"""对话式 MCP 管理工具：添加/查看/测试 MCP 服务器。"""

import asyncio
import time
from collections.abc import Callable

from langchain.tools import ToolRuntime, tool

from agents.context import PersonaAgentContext
from agents.tools.management import request_confirmation

Confirmer = Callable[[dict], bool]
_manager = None  # MCPManager | None


def set_mcp_manager(manager) -> None:
    global _manager
    _manager = manager


def get_mcp_manager():
    if _manager is None:
        raise RuntimeError("MCP 管理器尚未就绪")
    return _manager


def _build_config(*, name, transport, command, command_args, env, url, headers, description):
    from integrations.mcp.config import MCPServerConfig

    return MCPServerConfig(
        name=name.strip(),
        transport=transport,
        command=command.strip(),
        args=[str(item) for item in command_args],
        env={str(k): str(v) for k, v in env.items()},
        url=url.strip(),
        headers={str(k): str(v) for k, v in headers.items()},
        enabled=True,
        description=description.strip(),
        allowed_persona_ids=["*"],
    )


def add_mcp_server_core(*, config, manager, confirmer: Confirmer) -> dict:
    """核心流程：校验 → 确认 → 保存 → 热重连。"""
    try:
        config.validate(allow_arbitrary_stdio=getattr(manager, "_allow_arbitrary", False))
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    action = {
        "tool": "add_mcp_server",
        "title": f"添加 MCP 服务器 {config.name}",
        "target": config.name,
        "arguments": {
            "transport": config.transport,
            "command": config.command,
            "url": config.url,
            "description": config.description,
        },
    }
    if not confirmer(action):
        return {"status": "cancelled"}
    servers = manager.list_configs()
    if any(s.name == config.name for s in servers):
        return {"status": "error", "error": f"服务器已存在: {config.name}"}
    try:
        manager.save_configs(servers + [config])
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    state = asyncio.run(manager.reload_server(config.name))
    ok = state.get("status") != "error"
    return {
        "status": "ok" if ok else "error",
        "server": config.name,
        "state": state,
        "error": state.get("error", ""),
    }


def list_mcp_servers_core(manager) -> dict:
    statuses = manager.status()
    items = []
    for config in manager.list_configs():
        state = statuses.get(config.name, {"status": "not_loaded", "tool_count": 0, "error": ""})
        items.append(
            {
                "name": config.name,
                "transport": config.transport,
                "enabled": config.enabled,
                "description": config.description,
                "allowed_persona_ids": list(config.allowed_persona_ids),
                "state": state,
            }
        )
    return {"items": items}


def test_mcp_server_core(config, manager) -> dict:
    started = time.monotonic()
    try:
        infos = asyncio.run(manager.connect_server(config))
    except Exception as exc:
        return {
            "ok": False,
            "server": config.name,
            "error": str(exc),
            "tools": [],
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
    return {
        "ok": True,
        "server": config.name,
        "error": "",
        "tools": [info.name for info in infos],
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }


@tool("add_mcp_server")
def add_mcp_server(
    name: str,
    transport: str,
    command: str,
    command_args: list[str],
    env: dict[str, str],
    url: str,
    headers: dict[str, str],
    description: str,
    runtime: ToolRuntime[PersonaAgentContext],
) -> dict:
    """Add an MCP server configuration and hot-reconnect it; asks for confirmation."""
    config = _build_config(
        name=name,
        transport=transport,
        command=command or "",
        command_args=command_args or [],
        env=env or {},
        url=url or "",
        headers=headers or {},
        description=description or "",
    )
    return add_mcp_server_core(
        config=config,
        manager=get_mcp_manager(),
        confirmer=request_confirmation,
    )


@tool("list_mcp_servers")
def list_mcp_servers(runtime: ToolRuntime[PersonaAgentContext]) -> dict:
    """List configured MCP servers with connection state and persona grants."""
    return list_mcp_servers_core(get_mcp_manager())


@tool("test_mcp_server")
def test_mcp_server(name: str, runtime: ToolRuntime[PersonaAgentContext]) -> dict:
    """Test an MCP server connection without registering tools."""
    manager = get_mcp_manager()
    config = manager.get_config(name)
    if config is None:
        return {"ok": False, "server": name, "error": "服务器不存在", "tools": [], "elapsed_ms": 0}
    return test_mcp_server_core(config, manager)
