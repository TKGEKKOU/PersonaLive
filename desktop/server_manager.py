import threading
import time
from collections.abc import Callable

import httpx
import uvicorn
from fastapi import FastAPI

from settings import Settings


class ServerManager:
    def __init__(self, app_factory: Callable[[], FastAPI], settings: Settings | None = None) -> None:
        self.app_factory = app_factory
        self.settings = settings or Settings.load()
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.owns_server = False
        self.app: FastAPI | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.settings.app_port}"

    def is_running(self) -> bool:
        try:
            return httpx.get(f"{self.url}/api/health", timeout=1, trust_env=False).is_success
        except httpx.HTTPError:
            return False

    def start(self, timeout: int = 45) -> None:
        if self.is_running():
            return
        app = self.app_factory()
        self.app = app
        config = uvicorn.Config(app, host="127.0.0.1", port=self.settings.app_port, log_level="info")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True, name="personalive-api")
        self.thread.start()
        self.owns_server = True
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{self.url}/api/health", timeout=1, trust_env=False).is_success:
                    return
            except httpx.HTTPError:
                pass
            if not self.thread.is_alive():
                break
            time.sleep(0.5)
        self.stop()
        raise RuntimeError("PersonaLive 服务启动失败。")

    def stop(self) -> None:
        if not self.owns_server:
            return
        if self.server is not None:
            self.server.should_exit = True
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=10)
