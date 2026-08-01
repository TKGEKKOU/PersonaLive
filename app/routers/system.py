import os
import subprocess
import threading
import time

from fastapi import APIRouter, Body, Request

from app.routers.settings import require_local
from app.schemas import ShutdownPayload
from settings import Settings


router = APIRouter(prefix="/api/system", tags=["system"])


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
