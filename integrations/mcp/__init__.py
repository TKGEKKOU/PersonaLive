"""MCP 工具接入：外部 MCP 服务器 -> LangChain Tool -> ToolSpec。

设计目标与 AstrBot 的 MCP 能力对齐：用户在前端配置 MCP 服务器
（stdio / Streamable HTTP / SSE），应用启动时连接并把工具注册进
``agents.registry`` 的 ToolSpec 表；MCP 工具不会默认暴露给模型，
只有被 Skill 的 ``tool_names`` 引用后才会随技能加载进入上下文。
"""

from integrations.mcp.client import MCPManager, MCPToolInfo, classify_mcp_tool

__all__ = ["MCPManager", "MCPToolInfo", "classify_mcp_tool"]
