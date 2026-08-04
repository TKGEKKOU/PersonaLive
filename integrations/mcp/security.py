"""MCP stdio 启动命令安全校验。

防止误配或恶意配置在本机执行任意命令：白名单（可被
``MCP_ALLOW_ARBITRARY_STDIO=true`` 跳过）、黑名单与内联代码/危险参数
始终拦截。
"""

from __future__ import annotations


ALLOWED_STDIO_COMMANDS = frozenset({"python", "python3", "node", "uv", "deno", "bun"})
DENIED_STDIO_COMMANDS = frozenset(
    {
        "bash",
        "sh",
        "zsh",
        "fish",
        "powershell",
        "pwsh",
        "cmd",
        "rm",
        "rmdir",
        "sudo",
        "ssh",
        "scp",
        "curl",
        "wget",
        "nc",
        "ncat",
        "telnet",
        "kill",
        "pkill",
    }
)
INLINE_CODE_ARGS = frozenset({"-c", "-e", "--eval", "-eval"})
DOCKER_UNSAFE_ARGS = frozenset(
    {"--privileged", "--network=host", "--pid=host", "--ipc=host"}
)


def validate_stdio_config(
    command: str,
    args: list[str] | None,
    allow_arbitrary: bool = False,
) -> None:
    """校验 stdio 启动命令；违规时抛出带具体原因的 ValueError。"""

    raw = str(command or "").strip()
    cmd = raw.split()[0] if raw else ""
    if not cmd:
        raise ValueError("stdio 传输必须填写启动命令 command")
    if cmd in DENIED_STDIO_COMMANDS:
        raise ValueError(f"命令 {cmd} 在黑名单中，禁止作为 MCP 启动命令")
    if not allow_arbitrary and cmd not in ALLOWED_STDIO_COMMANDS:
        allowed = "/".join(sorted(ALLOWED_STDIO_COMMANDS))
        raise ValueError(
            f"命令 {cmd} 不在白名单（{allowed}），如需放行请设置 MCP_ALLOW_ARBITRARY_STDIO=true"
        )
    for arg in args or []:
        if arg in INLINE_CODE_ARGS:
            raise ValueError(f"参数 {arg} 禁止用于 MCP 启动（内联代码执行）")
        if arg in DOCKER_UNSAFE_ARGS:
            raise ValueError(f"参数 {arg} 存在安全风险，禁止使用")
