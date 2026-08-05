"""轻量 HTTP 服务：向启动页提供启动进度。

桌面应用启动流程中，Docker / 依赖阶段 FastAPI 尚未就绪，启动页无法通过
主服务轮询进度；此服务随桌面进程一起启动，在任何阶段都返回当前启动步骤，
并带 CORS 头放行 file:// 页面。绑定 127.0.0.1，仅本地可见。
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Callable


def progress_handler_factory(progress_fn: Callable[[], dict]):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, payload: dict, code: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.rstrip("/").endswith("/progress"):
                try:
                    self._send(progress_fn())
                except Exception as exc:  # pragma: no cover
                    self._send({"error": str(exc)}, 500)
            else:
                self._send({"error": "not found"}, 404)

        def log_message(self, *_args) -> None:
            pass

    return Handler


class LauncherProgressServer:
    def __init__(self, progress_fn: Callable[[], dict], port: int = 17100) -> None:
        self.port = port
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", port),
            progress_handler_factory(progress_fn),
        )
        self.thread = Thread(target=self.httpd.serve_forever, daemon=True, name="launcher-progress")
        self._started = False

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/progress"

    def start(self) -> None:
        if self._started:
            return
        self.thread.start()
        self._started = True

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass
