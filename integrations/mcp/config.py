"""MCP 服务器配置的持久化与校验。

配置文件：``data/mcp_servers.json``，为 JSON 数组，每项对应一个外部
MCP 服务器。写入采用临时文件 + ``os.replace`` 原子替换，避免写一半
损坏配置。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path


NAME_PATTERN = re.compile(r"[a-z0-9_-]+")
SUPPORTED_TRANSPORTS = ("stdio", "streamable_http", "sse")


@dataclass
class MCPServerConfig:
    """一个外部 MCP 服务器的配置。

    transport 决定连接方式：
    - ``stdio``：本地子进程，需要 command + args（可选 env）
    - ``streamable_http``：MCP 规范推荐的新远程传输，需要 url
    - ``sse``：旧版远程传输（兼容），需要 url
    """

    name: str
    transport: str = "stdio"
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    description: str = ""
    allowed_persona_ids: list[str] = field(default_factory=list)

    def validate(self, allow_arbitrary_stdio: bool = False) -> None:
        """校验配置字段；非法时抛出 ValueError 并说明原因。"""

        name = str(self.name or "").strip()
        if not NAME_PATTERN.fullmatch(name):
            raise ValueError("服务器名称须匹配 [a-z0-9_-]+")
        if self.transport not in SUPPORTED_TRANSPORTS:
            raise ValueError(f"transport 仅支持 {'/'.join(SUPPORTED_TRANSPORTS)}")
        if self.transport == "stdio":
            if not str(self.command or "").strip():
                raise ValueError("stdio 传输必须填写启动命令 command")
            from integrations.mcp.security import validate_stdio_config

            validate_stdio_config(
                self.command,
                self.args,
                allow_arbitrary=allow_arbitrary_stdio,
            )
        else:
            if not str(self.url or "").strip():
                raise ValueError("远程传输必须填写服务器地址 url")

    def to_connection(self) -> dict:
        """转换成 langchain-mcp-adapters 的 connection 配置字典。"""

        if self.transport == "stdio":
            return {
                "transport": "stdio",
                "command": self.command,
                "args": list(self.args),
                "env": dict(self.env) or None,
            }
        if self.transport == "sse":
            return {
                "transport": "sse",
                "url": self.url,
                "headers": dict(self.headers) or None,
            }
        return {
            "transport": "streamable_http",
            "url": self.url,
            "headers": dict(self.headers) or None,
        }


def load_servers(path: Path) -> list[MCPServerConfig]:
    """读取 MCP 服务器配置；文件缺失或损坏时返回空列表。"""

    if not Path(path).is_file():
        return []
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    servers: list[MCPServerConfig] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            servers.append(
                MCPServerConfig(
                    name=str(item.get("name") or ""),
                    transport=str(item.get("transport") or "stdio"),
                    command=str(item.get("command") or ""),
                    args=[str(a) for a in (item.get("args") or [])],
                    env={str(k): str(v) for k, v in (item.get("env") or {}).items()},
                    url=str(item.get("url") or ""),
                    headers={str(k): str(v) for k, v in (item.get("headers") or {}).items()},
                    enabled=bool(item.get("enabled", True)),
                    description=str(item.get("description") or ""),
                    allowed_persona_ids=[str(i) for i in (item.get("allowed_persona_ids") or [])],
                )
            )
        except (TypeError, ValueError):
            continue
    return servers


def default_servers() -> list[MCPServerConfig]:
    """首次启动默认预置：内置免 key 联网搜索（free-search）。

    free-search 默认引擎是 DuckDuckGo / Mojeek / GoogleNews / Bing，在国内网络
    环境下大多不可达，每次搜索会等满超时并返回空结果。这里把默认引擎换成
    国内可直连的百度 / 搜狗 / 360，保证开箱即用；海外网络同样可用（结果偏中文）。
    """

    return [
        MCPServerConfig(
            name="free-search",
            transport="stdio",
            command="uvx",
            args=["free-search-mcp"],
            env={
                "UV_DEFAULT_INDEX": "https://mirrors.aliyun.com/pypi/simple/",
                "SEARCH_MCP_DOWNLOAD_ENABLED": "false",
                "SEARCH_MCP_DEFAULT_ENGINES": "baidu,sogou,so360",
            },
            enabled=True,
            description="免 API key 联网搜索（本地优先）",
        )
    ]


def ensure_default_servers(path: Path) -> None:
    """配置文件不存在时写入默认服务器列表；已有配置保持不变。"""

    if not Path(path).is_file():
        save_servers(path, default_servers())


def save_servers(path: Path, servers: list[MCPServerConfig]) -> None:
    """原子写入 MCP 服务器配置（临时文件 + os.replace）。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(server) for server in servers]
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, target)
