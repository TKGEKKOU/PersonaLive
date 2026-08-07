"""Agent Skills 动态技能包：注册表、状态写入、中间件注入与端到端链路。"""

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.context import PersonaAgentContext
from persona.service import create_persona


def _context(**overrides):
    values = dict(
        persona_id="persona-a",
        workspace_id="local-default",
        knowledge_space_ids=("space-a",),
        conversation_id="thread-a",
        persona_name="Alpha",
        persona_type="character",
    )
    values.update(overrides)
    return PersonaAgentContext(**values)


class ToolCallingFake(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_skill_registry_lists_and_resolves_builtin_skill():
    from agents.registry import tool_specs
    from agents.skills import get_skill, list_skills

    names = {skill.name for skill in list_skills()}
    assert "document_management" in names

    skill = get_skill("document_management")
    known = {spec.name for spec in tool_specs()}
    assert skill.description
    assert skill.prompt_hint
    assert set(skill.tool_names) <= known

    with pytest.raises(KeyError):
        get_skill("does_not_exist")


def test_load_skill_tool_appends_to_state():
    from agents.workflow import build_persona_workflow

    model = ToolCallingFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "args": {"skill_name": "document_management"},
                        "id": "load-skill-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="资料管理技能已加载。"),
        ]
    )

    result = build_persona_workflow(model, MemorySaver()).invoke(
        {"messages": [("user", "帮我整理资料")], "active_worker": None},
        {"configurable": {"thread_id": "persona-a:thread-a"}},
        context=_context(),
    )

    assert result["loaded_skills"] == ["document_management"]
    assert result["messages"][-1].content == "资料管理技能已加载。"


def test_capability_summary_includes_available_skills():
    from agents.registry import capability_summary

    summary = capability_summary()
    assert "document_management" in summary
    assert "web-research" in summary
    assert "联网搜索默认使用 web-research 技能" in summary


def test_skill_middleware_exposes_loaded_skill_tools():
    from agents.workflow import build_persona_workflow

    seen_tools: list[list[str]] = []

    class RecordingFake(ToolCallingFake):
        def bind_tools(self, tools, **kwargs):
            seen_tools.append(sorted(tool.name for tool in tools if not isinstance(tool, dict)))
            return self

    model = RecordingFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "args": {"skill_name": "document_management"},
                        "id": "load-skill-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="好的。"),
        ]
    )

    build_persona_workflow(model, MemorySaver()).invoke(
        {"messages": [("user", "整理一下资料")], "active_worker": None},
        {"configurable": {"thread_id": "persona-a:thread-a"}},
        context=_context(),
    )

    assert any("list_persona_documents" in names for names in seen_tools)
    assert any("delegate_to_knowledge" in names for names in seen_tools)


def test_conversation_can_load_skill_and_call_its_tool(db_session):
    from agents.workflow import build_persona_workflow

    persona = create_persona(db_session, "Alpha")
    db_session.commit()
    context = _context(
        persona_id=persona.id,
        knowledge_space_ids=(persona.knowledge_space_id,),
        session_factory=lambda: db_session,
    )
    model = ToolCallingFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "args": {"skill_name": "document_management"},
                        "id": "load-skill-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "list_persona_documents",
                        "args": {},
                        "id": "list-docs-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="当前没有资料。"),
        ]
    )

    result = build_persona_workflow(model, MemorySaver()).invoke(
        {"messages": [("user", "帮我整理资料")], "active_worker": None},
        {"configurable": {"thread_id": "persona-a:thread-a"}},
        context=context,
    )

    assert result["loaded_skills"] == ["document_management"]
    assert result["messages"][-1].content == "当前没有资料。"
    assert any(
        message.name == "list_persona_documents"
        for message in result["messages"]
        if hasattr(message, "name")
    )


def test_skill_middleware_injects_instructions_into_system_message():
    from agents.workflow import build_persona_workflow

    captured_messages: list[list] = []

    class RecordingFake(ToolCallingFake):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            captured_messages.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = RecordingFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "load_skill",
                        "args": {"skill_name": "document_management"},
                        "id": "load-skill-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="好的。"),
        ]
    )

    build_persona_workflow(model, MemorySaver()).invoke(
        {"messages": [("user", "整理一下资料")], "active_worker": None},
        {"configurable": {"thread_id": "persona-a:thread-a"}},
        context=_context(),
    )

    assert len(captured_messages) >= 2
    last_messages = captured_messages[-1]
    system_text = "".join(
        str(message.content)
        for message in last_messages
        if getattr(message, "type", "") == "system"
    )
    assert "资料管理工具" in system_text
    assert "先向用户确认" in system_text


def test_custom_skill_crud_persists_and_refreshes(tmp_path, monkeypatch):
    import agents.skills as skills_module

    monkeypatch.setattr(skills_module, "USER_SKILL_DIR", tmp_path)

    created = skills_module.create_skill(
        name="data_analysis",
        description="数据分析",
        instructions="当用户要求分析数据时，先汇总关键指标再给出结论。",
        prompt_hint="分析数据时使用",
        tool_names=("search_persona_knowledge",),
    )
    assert created.name == "data_analysis"
    assert (tmp_path / "data_analysis" / "SKILL.md").is_file()

    loaded = skills_module.get_skill("data_analysis")
    assert loaded.instructions == "当用户要求分析数据时，先汇总关键指标再给出结论。"
    assert loaded.tool_names == ("search_persona_knowledge",)
    assert loaded.builtin is False

    skills_module.delete_skill("data_analysis")
    with pytest.raises(KeyError):
        skills_module.get_skill("data_analysis")
    assert not (tmp_path / "data_analysis").exists()


def test_create_skill_validates_input(tmp_path, monkeypatch):
    import agents.skills as skills_module

    monkeypatch.setattr(skills_module, "USER_SKILL_DIR", tmp_path)
    with pytest.raises(ValueError, match="name"):
        skills_module.create_skill(name="Bad Name!", instructions="x")
    with pytest.raises(ValueError, match="instructions"):
        skills_module.create_skill(name="valid_name", instructions="")
    with pytest.raises(ValueError, match="tool"):
        skills_module.create_skill(
            name="valid_name",
            instructions="x",
            tool_names=("no_such_tool",),
        )
    with pytest.raises(ValueError, match="内置"):
        skills_module.create_skill(
            name="document_management",
            instructions="x",
        )


def test_delete_builtin_skill_is_rejected():
    from agents.skills import delete_skill

    with pytest.raises(ValueError, match="内置"):
        delete_skill("document_management")


def test_skill_middleware_hides_ungranted_mcp_tool(tmp_path, monkeypatch):
    """未授权角色的对话中，引用 MCP 工具的 skill 不暴露该工具。"""

    import json as jsonlib

    from langchain_core.tools import tool as make_tool

    from agents import mcp_grants, skills as skills_mod
    from agents.registry import ToolSpec, register_tool_specs, unregister_tool_specs
    from agents.workflow import build_persona_workflow
    from integrations.mcp.config import MCPServerConfig, save_servers

    @make_tool
    def fs_add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    register_tool_specs(
        [ToolSpec(name="fs_add", specialist="mcp", tool=fs_add, server="fs")]
    )
    try:
        skill_dir = tmp_path / "skills"
        skill_dir.mkdir()
        monkeypatch.setattr(skills_mod, "USER_SKILL_DIR", skill_dir)
        (skill_dir / "fs_skill.json").write_text(
            jsonlib.dumps(
                {
                    "name": "fs_skill",
                    "description": "file tools",
                    "instructions": "使用文件工具",
                    "tool_names": ["fs_add"],
                    "prompt_hint": "文件操作",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        skills_mod.refresh_skills()

        grants_path = tmp_path / "mcp.json"
        save_servers(
            grants_path,
            [MCPServerConfig(name="fs", command="python", args=["s.py"])],
        )
        monkeypatch.setattr(mcp_grants, "_config_path", grants_path)
        mcp_grants.refresh_grants()

        seen: list[list[str]] = []

        class RecordingFake(ToolCallingFake):
            def bind_tools(self, tools, **kwargs):
                seen.append(
                    sorted(tool.name for tool in tools if not isinstance(tool, dict))
                )
                return self

        model = RecordingFake(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "load_skill",
                            "args": {"skill_name": "fs_skill"},
                            "id": "load-skill-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="好的。"),
            ]
        )
        build_persona_workflow(model, MemorySaver()).invoke(
            {"messages": [("user", "用文件工具")], "active_worker": None},
            {"configurable": {"thread_id": "persona-a:thread-a"}},
            context=_context(),
        )
        assert all("fs_add" not in names for names in seen)
    finally:
        unregister_tool_specs(["fs_add"])
        skills_mod.refresh_skills()


def test_scan_loads_standard_skill_dir_alongside_json(tmp_path, monkeypatch):
    import agents.skills as skills_mod

    directory = tmp_path / "skills"
    directory.mkdir()
    (directory / "legacy.json").write_text(
        '{"name": "legacy", "description": "d", "instructions": "i", "tool_names": ["search_persona_knowledge"]}',
        encoding="utf-8",
    )
    package = directory / "pdf-tools"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\nname: pdf-tools\ndescription: PDF tools. Use when handling PDFs.\n"
        "tool-names: [search_persona_knowledge]\n---\nExtract text.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(skills_mod, "USER_SKILL_DIR", directory)
    skills_mod.refresh_skills()
    try:
        legacy = skills_mod.get_skill("legacy")
        standard = skills_mod.get_skill("pdf-tools")
        assert legacy.format == "json"
        assert standard.format == "skillmd"
        assert standard.metadata == {}
        assert standard.instructions == "Extract text."
    finally:
        skills_mod.refresh_skills()


def test_delete_skill_removes_directory_package(tmp_path, monkeypatch):
    import agents.skills as skills_mod

    directory = tmp_path / "skills"
    directory.mkdir()
    package = directory / "pdf-tools"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\nname: pdf-tools\ndescription: x\n---\nbody", encoding="utf-8"
    )
    monkeypatch.setattr(skills_mod, "USER_SKILL_DIR", directory)
    skills_mod.refresh_skills()
    try:
        assert skills_mod.delete_skill("pdf-tools") is True
        assert not package.exists()
    finally:
        skills_mod.refresh_skills()


def test_web_research_skill_appears_when_mcp_tools_registered():
    """内置 web-research 技能引用 MCP 工具；工具注册后才可见，注销后隐藏。"""

    from langchain_core.tools import tool as make_tool

    from agents import skills as skills_mod
    from agents.registry import ToolSpec, register_tool_specs, unregister_tool_specs

    @make_tool
    def search(query: str) -> str:
        """Search the web."""
        return "ok"

    @make_tool
    def research(question: str) -> str:
        """Research a question."""
        return "ok"

    register_tool_specs(
        [
            ToolSpec(name="search", specialist="mcp", tool=search, server="free-search"),
            ToolSpec(
                name="research", specialist="mcp", tool=research, server="free-search"
            ),
        ]
    )
    try:
        skills_mod.refresh_skills()
        skill = skills_mod.get_skill("web-research")
        assert skill.format == "skillmd"
        assert set(skill.tool_names) == {"search", "research"}
    finally:
        unregister_tool_specs(["search", "research"])
        skills_mod.refresh_skills()
        with pytest.raises(KeyError):
            skills_mod.get_skill("web-research")


def test_web_research_tools_auto_loaded_without_load_skill(tmp_path, monkeypatch):
    """free-search 启用且角色已授权时，search/research 无需 load_skill 即对 Supervisor 可见。"""

    from langchain_core.tools import tool as make_tool

    from agents import mcp_grants, skills as skills_mod
    from agents.registry import ToolSpec, register_tool_specs, unregister_tool_specs
    from agents.workflow import build_persona_workflow
    from integrations.mcp.config import MCPServerConfig, save_servers

    @make_tool
    def search(query: str) -> str:
        """Search the web."""
        return "ok"

    @make_tool
    def research(question: str) -> str:
        """Research a question."""
        return "ok"

    register_tool_specs(
        [
            ToolSpec(name="search", specialist="mcp", tool=search, server="free-search"),
            ToolSpec(
                name="research", specialist="mcp", tool=research, server="free-search"
            ),
        ]
    )
    skills_mod.refresh_skills()
    grants_path = tmp_path / "mcp.json"
    save_servers(
        grants_path,
        [
            MCPServerConfig(
                name="free-search",
                command="uvx",
                args=["free-search-mcp"],
                allowed_persona_ids=["persona-a"],
            )
        ],
    )
    monkeypatch.setattr(mcp_grants, "_config_path", grants_path)
    mcp_grants.refresh_grants()
    seen: list[list[str]] = []

    class RecordingFake(ToolCallingFake):
        def bind_tools(self, tools, **kwargs):
            seen.append(sorted(tool.name for tool in tools if not isinstance(tool, dict)))
            return self

    model = RecordingFake(responses=[AIMessage(content="好的。")])
    try:
        build_persona_workflow(model, MemorySaver()).invoke(
            {"messages": [("user", "潍坊今天气温多少")], "active_worker": None},
            {"configurable": {"thread_id": "persona-a:thread-a"}},
            context=_context(),
        )
        assert seen
        assert "search" in seen[0]
        assert "research" in seen[0]
    finally:
        unregister_tool_specs(["search", "research"])
        skills_mod.refresh_skills()
        mcp_grants.refresh_grants()


def test_land_skill_copies_package_files(tmp_path):
    from pathlib import Path

    from agents.skills import RUNTIME_SKILL_DIR, land_skill, list_skills, refresh_skills

    name = "land-test-skill"
    try:
        target = Path("data/skills") / name
        (target / "scripts").mkdir(parents=True)
        (target / "scripts" / "a.py").write_text("print('a')\n", encoding="utf-8")
        (target / "assets").mkdir(parents=True)
        (target / "assets" / "x.txt").write_text("x", encoding="utf-8")
        (target / "SKILL.md").write_text(
            "---\nname: land-test-skill\ndescription: landing test.\ntool-names: []\n---\nbody\n",
            encoding="utf-8",
        )
        refresh_skills()
        skill = next(s for s in list_skills() if s.name == name)
        runtime_dir = land_skill(skill)
        assert (runtime_dir / "scripts" / "a.py").is_file()
        assert (runtime_dir / "assets" / "x.txt").is_file()
        assert RUNTIME_SKILL_DIR.name == "runtime"
        # 幂等：再次落地不报错
        assert land_skill(skill) == runtime_dir
    finally:
        import shutil

        shutil.rmtree(Path("data/skills") / name, ignore_errors=True)
        shutil.rmtree(RUNTIME_SKILL_DIR / name, ignore_errors=True)
        refresh_skills()


def test_create_skill_writes_skillmd_package(tmp_path):
    from pathlib import Path

    from agents.skills import USER_SKILL_DIR, create_skill, get_skill, refresh_skills, update_skill

    name = "create-skillmd-test"
    try:
        created = create_skill(
            name=name,
            description="创建测试",
            instructions="当用户需要时执行。",
            prompt_hint="提示词",
            tool_names=(),
        )
        assert created.format == "skillmd"
        skill_file = Path(USER_SKILL_DIR) / name / "SKILL.md"
        assert skill_file.is_file()
        assert "name: create-skillmd-test" in skill_file.read_text(encoding="utf-8")
        refresh_skills()
        assert get_skill(name).prompt_hint == "提示词"

        legacy_dir = Path(USER_SKILL_DIR)
        legacy_dir.mkdir(parents=True, exist_ok=True)
        (legacy_dir / "legacy-skill.json").write_text(
            '{"name": "legacy-skill", "description": "old", "instructions": "x", "tool_names": []}',
            encoding="utf-8",
        )
        refresh_skills()
        try:
            update_skill("legacy-skill", instructions="y")
        except ValueError as exc:
            assert "只读" in str(exc)
        else:
            raise AssertionError("legacy JSON 技能应拒绝编辑")
    finally:
        import shutil

        shutil.rmtree(Path(USER_SKILL_DIR) / name, ignore_errors=True)
        (Path(USER_SKILL_DIR) / f"{name}.json").unlink(missing_ok=True)
        (Path(USER_SKILL_DIR) / "legacy-skill.json").unlink(missing_ok=True)
        refresh_skills()


def test_document_management_is_skillmd_package():
    from agents.skills import get_skill, list_skills

    names = {skill.name for skill in list_skills()}
    assert "document_management" in names
    skill = get_skill("document_management")
    assert skill.format == "skillmd"
    assert skill.tool_names == (
        "list_persona_documents",
        "add_persona_knowledge",
        "delete_persona_document",
    )


def test_loaded_scripted_skill_exposes_run_skill_script(tmp_path):
    import shutil
    from pathlib import Path

    from agents.skills import RUNTIME_SKILL_DIR, refresh_skills

    name = "scripted-demo"
    try:
        package = Path("data/skills") / name
        (package / "scripts").mkdir(parents=True)
        (package / "scripts" / "demo.py").write_text("print('demo')\n", encoding="utf-8")
        (package / "SKILL.md").write_text(
            "---\nname: scripted-demo\ndescription: demo with scripts.\ntool-names: []\n---\nbody\n",
            encoding="utf-8",
        )
        refresh_skills()

        from langgraph.checkpoint.memory import MemorySaver

        from agents.workflow import build_persona_workflow

        model = ToolCallingFake(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "load_skill",
                            "args": {"skill_name": name},
                            "id": "load-demo-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "run_skill_script",
                            "args": {"skill_name": name, "script": "demo.py", "script_args": []},
                            "id": "run-demo-1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="完成。"),
            ]
        )
        result = build_persona_workflow(model, MemorySaver()).invoke(
            {"messages": [("user", "运行 demo")], "active_worker": None},
            {"configurable": {"thread_id": "persona-a:thread-a"}},
            context=_context(),
        )
        interrupts = result.get("__interrupt__") or ()
        assert interrupts and interrupts[0].value.get("tool") == "run_skill_script"
    finally:
        shutil.rmtree(Path("data/skills") / name, ignore_errors=True)
        shutil.rmtree(RUNTIME_SKILL_DIR / name, ignore_errors=True)
        refresh_skills()


def test_list_installable_skills_reads_catalog():
    from agents.skills import list_installable_skills

    data = list_installable_skills()
    assert isinstance(data["items"], list)
    for item in data["items"]:
        assert item["name"]
        assert "installed" in item


def test_install_skill_tool_reachable_from_supervisor(tmp_path, monkeypatch):
    import agents.tools.skill_install as skill_install_module
    from langgraph.checkpoint.memory import MemorySaver

    from agents.workflow import build_persona_workflow

    package = tmp_path / "pdf-tools"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\nname: pdf-tools\ndescription: x.\ntool-names: []\n---\nbody\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        skill_install_module,
        "fetch_github_skill",
        lambda *a, **k: package,
    )

    model = ToolCallingFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "install_skill",
                        "args": {
                            "source_type": "github",
                            "repo": "openai/skills",
                            "path": "skills/pdf-tools",
                            "ref": "main",
                            "url": "",
                        },
                        "id": "install-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="已安装。"),
        ]
    )
    result = build_persona_workflow(model, MemorySaver()).invoke(
        {"messages": [("user", "帮我安装 pdf-tools 技能")], "active_worker": None},
        {"configurable": {"thread_id": "persona-a:thread-a"}},
        context=_context(),
    )
    interrupts = result.get("__interrupt__") or ()
    assert interrupts and interrupts[0].value.get("tool") == "install_skill"


def test_new_builtin_skills_are_valid():
    from agents.skills import get_skill, list_skills

    names = {skill.name for skill in list_skills()}
    for name in ("memory_management", "profile_management", "reply_conventions"):
        assert name in names
        skill = get_skill(name)
        assert skill.format == "skillmd"
        assert skill.metadata.get("category")
