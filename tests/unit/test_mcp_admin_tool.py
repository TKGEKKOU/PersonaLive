"""mcp_admin 工具测试：校验、确认与成功路径。"""

from integrations.mcp.config import MCPServerConfig


class FakeManager:
    def __init__(self):
        self.servers = []
        self.saved = []
        self.reloaded = []
        self._allow_arbitrary = False
        self._status = {}

    def list_configs(self):
        return list(self.servers)

    def save_configs(self, servers):
        self.saved.append([s.name for s in servers])
        self.servers = list(servers)

    async def reload_server(self, name):
        self.reloaded.append(name)
        return {"status": "connected", "tool_count": 2, "error": ""}

    async def connect_server(self, config):
        return [type("I", (), {"name": "tool_a", "description": "d", "requires_confirmation": False, "mutates_data": False})()]

    def status(self):
        return {s.name: {"status": "connected", "tool_count": 0, "error": ""} for s in self.servers}


def _config(**overrides):
    values = dict(
        name="filesystem",
        transport="stdio",
        command="python",
        args=["-m", "demo_mcp", "D:/data"],
    )
    values.update(overrides)
    return MCPServerConfig(**values)


def test_add_mcp_server_core_saves_and_reloads():
    from agents.tools.mcp_admin import add_mcp_server_core

    manager = FakeManager()
    result = add_mcp_server_core(
        config=_config(),
        manager=manager,
        confirmer=lambda action: True,
    )
    assert result["status"] == "ok"
    assert manager.saved and manager.saved[0][-1] == "filesystem"
    assert manager.reloaded == ["filesystem"]


def test_add_mcp_server_core_rejects_invalid_and_existing():
    from agents.tools.mcp_admin import add_mcp_server_core

    manager = FakeManager()
    result = add_mcp_server_core(
        config=_config(transport="streamable_http", url=""),
        manager=manager,
        confirmer=lambda action: True,
    )
    assert result["status"] == "error"
    assert "url" in result["error"]

    manager.servers = [_config()]
    result = add_mcp_server_core(
        config=_config(),
        manager=manager,
        confirmer=lambda action: True,
    )
    assert result["status"] == "error"
    assert "已存在" in result["error"]


def test_add_mcp_server_core_cancels():
    from agents.tools.mcp_admin import add_mcp_server_core

    manager = FakeManager()
    result = add_mcp_server_core(
        config=_config(),
        manager=manager,
        confirmer=lambda action: False,
    )
    assert result["status"] == "cancelled"
    assert manager.saved == []


def test_list_and_test_cores():
    from agents.tools.mcp_admin import list_mcp_servers_core, test_mcp_server_core

    manager = FakeManager()
    manager.servers = [_config()]
    listing = list_mcp_servers_core(manager)
    assert listing["items"][0]["name"] == "filesystem"
    tested = test_mcp_server_core(_config(), manager)
    assert tested["ok"] is True
    assert tested["tools"] == ["tool_a"]


def test_add_mcp_server_reachable_from_supervisor(monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver

    from agents.context import PersonaAgentContext
    from agents.tools.mcp_admin import set_mcp_manager
    from agents.workflow import build_persona_workflow

    manager = FakeManager()
    set_mcp_manager(manager)
    try:
        class ToolCallingFake(FakeMessagesListChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        model = ToolCallingFake(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "add_mcp_server",
                            "args": {
                                "name": "filesystem",
                                "transport": "stdio",
                                "command": "python",
                                "command_args": ["-m", "demo_mcp", "D:/data"],
                                "env": {},
                                "url": "",
                                "headers": {},
                                "description": "",
                            },
                            "id": "mcp-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="已添加。"),
            ]
        )
        context = PersonaAgentContext(
            persona_id="persona-a",
            workspace_id="local-default",
            knowledge_space_ids=("space-a",),
            conversation_id="thread-a",
            persona_name="Alpha",
            persona_type="character",
        )
        result = build_persona_workflow(model, MemorySaver()).invoke(
            {"messages": [("user", "加一个 filesystem MCP")], "active_worker": None},
            {"configurable": {"thread_id": "persona-a:thread-a"}},
            context=context,
        )
        interrupts = result.get("__interrupt__") or ()
        assert interrupts and interrupts[0].value.get("tool") == "add_mcp_server"
    finally:
        set_mcp_manager(None)
