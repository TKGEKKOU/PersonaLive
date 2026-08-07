"""MCP stdio 安全校验规则测试。"""

import pytest

from integrations.mcp.security import validate_stdio_config


def test_valid_python_passes():
    validate_stdio_config("python", ["server.py"])
    validate_stdio_config("uv", ["run", "server.py"])
    validate_stdio_config("uvx", ["free-search-mcp"])


def test_allowlist_rejects_unknown_command():
    with pytest.raises(ValueError, match="不在白名单"):
        validate_stdio_config("custom-bin", ["x"])


def test_blocklist_always_rejected():
    for cmd in ("bash", "sh", "powershell", "rm", "sudo", "ssh", "curl"):
        with pytest.raises(ValueError, match="黑名单"):
            validate_stdio_config(cmd, [])


def test_inline_code_rejected():
    with pytest.raises(ValueError, match="内联代码"):
        validate_stdio_config("python", ["-c", "print(1)"])
    with pytest.raises(ValueError, match="内联代码"):
        validate_stdio_config("node", ["-e", "eval()"])


def test_unsafe_flags_rejected_even_for_allowed_command():
    with pytest.raises(ValueError, match="安全风险"):
        validate_stdio_config("python", ["server.py", "--privileged"])
    with pytest.raises(ValueError, match="安全风险"):
        validate_stdio_config("python", ["server.py", "--network=host"])


def test_allow_arbitrary_skips_allowlist_but_not_blocklist():
    validate_stdio_config("any-cmd", ["x"], allow_arbitrary=True)
    with pytest.raises(ValueError, match="黑名单"):
        validate_stdio_config("bash", [], allow_arbitrary=True)
