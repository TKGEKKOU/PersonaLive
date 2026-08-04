"""MCP 服务器管理 API。

前端"插件"页的 MCP 面板调用这里：服务器配置的增删改查、即时连接测试，
以及查看已注册的 MCP 工具。配置保存后需重启应用生效（与插件一致）。
"""

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from integrations.mcp.config import MCPServerConfig


router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class MCPServerPayload(BaseModel):
    name: str = Field(..., description="服务器名称，匹配 [a-z0-9_-]+")
    transport: str = Field("stdio", description="stdio / streamable_http / sse")
    command: str = Field("", description="stdio 传输的启动命令")
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = Field("", description="远程传输的服务器地址")
    headers: dict[str, str] = Field(default_factory=dict)
    enabled: bool = True
    description: str = ""


def _manager(request: Request):
    manager = getattr(request.app.state, "mcp_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="MCP 管理器尚未就绪")
    return manager


def _to_dict(config: MCPServerConfig, status_info: dict | None = None) -> dict:
    payload = {
        "name": config.name,
        "transport": config.transport,
        "command": config.command,
        "args": list(config.args),
        "env": dict(config.env),
        "url": config.url,
        "headers": dict(config.headers),
        "enabled": config.enabled,
        "description": config.description,
        "allowed_persona_ids": list(config.allowed_persona_ids),
    }
    if status_info is not None:
        payload["status"] = status_info
    return payload


@router.get("/servers")
def list_servers_api(request: Request) -> list[dict]:
    """服务器列表（含连接状态），供前端渲染。"""

    manager = _manager(request)
    statuses = manager.status()
    return [
        _to_dict(config, statuses.get(config.name, {"status": "not_loaded", "tool_count": 0, "error": ""}))
        for config in manager.list_configs()
    ]


@router.post("/servers", status_code=status.HTTP_201_CREATED)
async def upsert_server_api(request: Request, payload: MCPServerPayload) -> dict:
    """新增或更新服务器配置；保存后自动热重连（无需重启应用）。"""

    manager = _manager(request)
    config = MCPServerConfig(
        name=payload.name.strip(),
        transport=payload.transport,
        command=payload.command.strip(),
        args=[str(item) for item in payload.args],
        env={str(k): str(v) for k, v in payload.env.items()},
        url=payload.url.strip(),
        headers={str(k): str(v) for k, v in payload.headers.items()},
        enabled=payload.enabled,
        description=payload.description.strip(),
    )
    try:
        servers = manager.list_configs()
        existing = [s for s in servers if s.name == config.name]
        remaining = [s for s in servers if s.name != config.name]
        manager.save_configs(remaining + [config] if existing else servers + [config])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await manager.reload_server(config.name)
    return _to_dict(config)


@router.delete("/servers/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_server_api(request: Request, name: str) -> Response:
    """删除服务器配置。"""

    manager = _manager(request)
    servers = manager.list_configs()
    remaining = [s for s in servers if s.name != name]
    if len(remaining) == len(servers):
        raise HTTPException(status_code=404, detail="MCP 服务器不存在")
    manager.save_configs(remaining)
    manager.disable_server(name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/servers/{name}/enable")
async def enable_server_api(request: Request, name: str) -> dict:
    """即时启用并连接服务器，注册其工具。"""

    manager = _manager(request)
    config = manager.get_config(name)
    if config is None:
        raise HTTPException(status_code=404, detail="MCP 服务器不存在")
    try:
        return await manager.enable_server(config)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/servers/{name}/disable")
def disable_server_api(request: Request, name: str) -> dict:
    """即时停用服务器，注销其已注册工具。"""

    manager = _manager(request)
    if manager.get_config(name) is None:
        raise HTTPException(status_code=404, detail="MCP 服务器不存在")
    return manager.disable_server(name)


@router.post("/servers/{name}/reload")
async def reload_server_api(request: Request, name: str) -> dict:
    """按当前配置重连服务器（失败时状态为 error 并保留原因）。"""

    manager = _manager(request)
    try:
        return await manager.reload_server(name)
    except KeyError:
        raise HTTPException(status_code=404, detail="MCP 服务器不存在") from None


@router.post("/servers/{name}/test")
async def test_server_api(request: Request, name: str) -> dict:
    """即时测试连接（不注册工具），返回工具清单与耗时。"""

    manager = _manager(request)
    config = manager.get_config(name)
    if config is None:
        raise HTTPException(status_code=404, detail="MCP 服务器不存在")
    import time

    started = time.monotonic()
    try:
        infos = await manager.connect_server(config)
    except Exception as exc:
        return {
            "ok": False,
            "server": name,
            "error": str(exc),
            "tool_count": 0,
            "tools": [],
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        }
    return {
        "ok": True,
        "server": name,
        "error": "",
        "tool_count": len(infos),
        "tools": [
            {
                "name": info.name,
                "description": info.description,
                "requires_confirmation": info.requires_confirmation,
                "mutates_data": info.mutates_data,
            }
            for info in infos
        ],
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
    }


@router.get("/tools")
def list_registered_tools_api(request: Request) -> list[dict]:
    """当前已注册的 MCP 工具（应用启动时连接成功的服务器）。"""

    manager = _manager(request)
    return [
        {
            "name": info.name,
            "description": info.description,
            "server": info.server,
            "requires_confirmation": info.requires_confirmation,
            "mutates_data": info.mutates_data,
        }
        for info in manager.registered_tools()
    ]
