import os
import subprocess
import threading
import time

from fastapi import APIRouter, Body, Request

from app.routers.settings import require_local
from app.schemas import DockerSettingsPayload, ShutdownPayload
from extensions.storage import read_json, write_json
from settings import Settings


router = APIRouter(prefix="/api/system", tags=["system"])
DOCKER_SETTINGS_PATH = Settings.load().project_root / "data" / "docker_settings.json"


def _docker_settings() -> dict:
    values = read_json(DOCKER_SETTINGS_PATH)
    on_exit = values.get("on_exit")
    if on_exit not in {"keep", "pause", "remove"}:
        on_exit = "keep"
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
    """仅本机可用：延迟退出当前 PersonaLive 进程（桌面版连同窗口一起退出）。
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
