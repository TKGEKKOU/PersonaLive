import os
import shutil
import subprocess
import threading
import time

from fastapi import APIRouter, Body, HTTPException, Request
from sqlalchemy import select

from app.routers.settings import require_local
from app.schemas import DockerSettingsPayload, ShutdownPayload
from extensions.storage import read_json, write_json
from integrations.mcp.config import MCPServerConfig
from app.models import Persona
from persona.service import LOCAL_WORKSPACE_ID
from settings import Settings


router = APIRouter(prefix="/api/system", tags=["system"])
DOCKER_SETTINGS_PATH = Settings.load().project_root / "data" / "docker_settings.json"
KEYLESS_SERVER_NAME = "free-search"


def _docker_settings() -> dict:
    values = read_json(DOCKER_SETTINGS_PATH)
    on_exit = values.get("on_exit", "pause")
    if on_exit not in {"keep", "pause", "remove"}:
        on_exit = "pause"
    return {"on_exit": on_exit}


def _run_compose(command: str) -> dict:
    try:
        result = subprocess.run(
            ["docker", "compose", command],
            cwd=Settings.load().project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return {"ok": False, "error": detail or f"docker compose {command} 失败"}
    return {"ok": True}


def _mcp_manager(request: Request):
    manager = getattr(request.app.state, "mcp_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="MCP 管理器尚未就绪")
    return manager


def _all_persona_ids(request: Request) -> list[str]:
    """返回本地工作区全部角色 ID（用于启用平台级 MCP 服务器时自动授权）。"""

    factory = getattr(request.app.state, "session_factory", None)
    if factory is None:
        return []
    try:
        session = factory()
        try:
            rows = session.scalars(
                select(Persona.id).where(Persona.workspace_id == LOCAL_WORKSPACE_ID)
            )
            return list(rows)
        finally:
            session.close()
    except Exception:
        return []


@router.get("/web-search-keyless")
def web_search_keyless_status(request: Request) -> dict:
    """返回免 key 搜索当前状态（是否配置、uvx 可用性、连接状态）。"""

    require_local(request)
    manager = _mcp_manager(request)
    config = manager.get_config(KEYLESS_SERVER_NAME)
    status = manager.status().get(KEYLESS_SERVER_NAME, {})
    return {
        "enabled": config is not None,
        "uvx_available": shutil.which("uvx") is not None,
        "status": status.get("status", "not_loaded"),
        "tool_count": status.get("tool_count", 0),
        "error": status.get("error", ""),
    }


@router.post("/web-search-keyless")
async def web_search_keyless_set(request: Request, payload: dict) -> dict:
    """启用/关闭内置免 key 搜索（free-search-mcp）；变更即时生效。"""

    require_local(request)
    manager = _mcp_manager(request)
    enabled = bool(payload.get("enabled"))
    servers = manager.list_configs()
    if enabled:
        if shutil.which("uvx") is None:
            raise HTTPException(status_code=422, detail="未检测到 uvx，请先安装 uv")
        config = MCPServerConfig(
            name=KEYLESS_SERVER_NAME,
            transport="stdio",
            command="uvx",
            args=["free-search-mcp"],
            env={
                "UV_DEFAULT_INDEX": "https://mirrors.aliyun.com/pypi/simple/",
                "SEARCH_MCP_DOWNLOAD_ENABLED": "false",
            },
            enabled=True,
            description="免 API key 联网搜索（本地优先）",
            # 平台级只读能力：启用时自动授权所有现有角色，避免因
            # fail-closed 角色可见性导致工具不可见；新建角色需在角色管理页授权。
            allowed_persona_ids=_all_persona_ids(request),
        )
        manager.save_configs(
            [s for s in servers if s.name != KEYLESS_SERVER_NAME] + [config]
        )
        await manager.reload_server(KEYLESS_SERVER_NAME)
    else:
        remaining = [s for s in servers if s.name != KEYLESS_SERVER_NAME]
        manager.save_configs(remaining)
        manager.disable_server(KEYLESS_SERVER_NAME)
    from agents.skills import refresh_skills

    refresh_skills()
    return web_search_keyless_status(request)


@router.get("/docker-settings")
def get_docker_settings(request: Request) -> dict:
    require_local(request)
    return _docker_settings()


@router.put("/docker-settings")
def update_docker_settings(payload: DockerSettingsPayload, request: Request) -> dict:
    require_local(request)
    write_json(DOCKER_SETTINGS_PATH, {"on_exit": payload.on_exit})
    return _docker_settings()


@router.post("/docker/pause")
def pause_docker(request: Request) -> dict:
    require_local(request)
    return _run_compose("stop")


@router.post("/docker/remove")
def remove_docker(request: Request) -> dict:
    require_local(request)
    return _run_compose("down")


@router.post("/shutdown")
def shutdown(
    payload: ShutdownPayload = Body(default=ShutdownPayload()),
    request: Request = ...,
) -> dict:
    """仅本机可用：延迟退出当前 YUMENO 进程（桌面版连同窗口一起退出）。
    stop_docker=True 时先执行 docker compose stop（暂停容器、不删除）再退出。"""
    require_local(request)

    def stop() -> None:
        time.sleep(0.5)
        callback = getattr(request.app.state, "shutdown_callback", None)
        if callback is not None:
            # 桌面模式：停服务（可选暂停 Docker），窗口回到启动页，不退出进程
            callback(stop_docker=payload.stop_docker)
            return
        if payload.stop_docker:
            try:
                subprocess.run(
                    ["docker", "compose", "stop"],
                    cwd=Settings.load().project_root,
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
            except Exception:
                pass
        os._exit(0)

    threading.Thread(target=stop, daemon=True, name="personalive-shutdown").start()
    return {"status": "stopping"}
