import os
import subprocess
import sys
from pathlib import Path


def open_resource_directory(path: Path) -> str:
    """创建并打开本地资源目录，返回可展示给前端的绝对路径。"""
    resolved = path.resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", str(resolved)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(resolved)])
    else:
        subprocess.Popen(["xdg-open", str(resolved)])
    return str(resolved)
