import os
import threading
import time

from fastapi import APIRouter, Request

from app.routers.settings import require_local


router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/shutdown")
def shutdown(request: Request) -> dict:
    """仅本机可用：延迟退出当前 PersonaLive 进程（桌面版连同窗口一起退出）。"""
    require_local(request)

    def stop() -> None:
        time.sleep(0.5)
        os._exit(0)

    threading.Thread(target=stop, daemon=True, name="personalive-shutdown").start()
    return {"status": "stopping"}
