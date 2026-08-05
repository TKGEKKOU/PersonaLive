"""MCP 角色授权查询。

按服务器粒度控制角色可见性：未授权角色即使技能引用对应工具，工具也
不可见（fail-closed）。授权映射读取 ``data/mcp_servers.json`` 并缓存，
配置保存后调用 ``refresh_grants()`` 立即生效。
"""

from __future__ import annotations

from pathlib import Path

from integrations.mcp.config import GLOBAL_ALL, load_servers
from settings import Settings


_config_path: Path = Settings.load().project_root / "data" / "mcp_servers.json"
_grants: dict[str, frozenset[str]] = {}
_global_servers: frozenset[str] = frozenset()


def refresh_grants() -> None:
    """重新扫描服务器配置，重建 角色 → 授权服务器 映射。

    ``allowed_persona_ids`` 含全局通配 ``*`` 的服务器对所有角色可见
    （含未来新建角色）；其余仍按角色 ID 精确匹配，未配置保持 fail-closed。
    """

    global _grants, _global_servers
    grants: dict[str, set[str]] = {}
    global_servers: set[str] = set()
    for server in load_servers(_config_path):
        if GLOBAL_ALL in server.allowed_persona_ids:
            global_servers.add(server.name)
        for persona_id in server.allowed_persona_ids:
            if persona_id == GLOBAL_ALL:
                continue
            grants.setdefault(persona_id, set()).add(server.name)
    _grants = {pid: frozenset(names) for pid, names in grants.items()}
    _global_servers = frozenset(global_servers)


def allowed_servers_for_persona(persona_id: str) -> frozenset[str]:
    """返回角色被授权的服务器集合；未配置任何授权时为空（fail-closed）。"""

    return _grants.get(persona_id, frozenset()) | _global_servers


def server_for_tool(tool_name: str) -> str:
    """返回工具所属 MCP 服务器；非 MCP 工具返回空字符串。"""

    from agents.registry import tool_specs

    return next(
        (spec.server for spec in tool_specs() if spec.name == tool_name),
        "",
    )


def is_mcp_tool_visible(persona_id: str, tool_name: str) -> bool:
    """判断工具对角色可见：非 MCP 工具恒可见；MCP 工具须角色已授权。"""

    server = server_for_tool(tool_name)
    if not server:
        return True
    return server in allowed_servers_for_persona(persona_id)


refresh_grants()
