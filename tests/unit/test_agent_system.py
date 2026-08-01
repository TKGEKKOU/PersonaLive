from app.models import DocumentJob
import json

from langchain.messages import AIMessage, ToolMessage
from agents.context import PersonaAgentContext
from agents.registry import READ_ONLY_TOOL_NAMES, tool_specs
from agents.supervisor import route_specialist, specialist_prompt
from agents.service import PersonaAgentService, is_capability_question
from agents.tools.knowledge import run_persona_knowledge_search
from agents.tools.management import list_documents_for_context
from persona.service import create_persona
from rag.service import RagResult


def test_worker_tools_are_limited_to_registered_owner():
    from agents.workflow import worker_tools

    assert [tool.name for tool in worker_tools("web")] == ["web_search"]
    assert "search_persona_knowledge" not in [tool.name for tool in worker_tools("web")]


def test_persona_workflow_has_supervisor_and_worker_nodes():
    from langgraph.checkpoint.memory import MemorySaver

    from agents.workflow import WORKERS, build_persona_workflow

    graph = build_persona_workflow(model=None, checkpointer=MemorySaver())
    nodes = graph.get_graph().nodes

    assert "persona_supervisor" in nodes
    for worker in WORKERS:
        assert f"{worker}_worker" in nodes
        assert f"finalize_{worker}" in nodes


def test_supervisor_prompt_includes_full_persona_profile_and_fact_first_rules():
    from agents.workflow import _supervisor_prompt

    context = PersonaAgentContext(
        persona_id="persona-a",
        workspace_id="local-default",
        knowledge_space_ids=("space-a",),
        conversation_id="thread-a",
        persona_name="Ames",
        persona_type="character",
        persona_profile={"voice": "calm and observant", "style": "state conclusions first"},
    )

    prompt = _supervisor_prompt(context)

    assert '"voice": "calm and observant"' in prompt
    assert "Answer the user's question directly before offering advice." in prompt
    assert "weather, news, or other factual requests" in prompt
    assert "status=accepted" in prompt
    assert "status=insufficient" in prompt


def test_supervisor_prompt_limits_tts_enabled_chat_length():
    from agents.workflow import _supervisor_prompt

    context = PersonaAgentContext(
        persona_id="persona-a", workspace_id="local-default", knowledge_space_ids=("space-a",),
        conversation_id="thread-a", persona_name="Ames", persona_type="character",
        persona_profile={"tts": {"enabled": True}},
    )

    prompt = _supervisor_prompt(context)
    assert "ordinary chat" in prompt
    assert "around 30 Chinese characters" in prompt
    assert "never exceeding 50" in prompt
    assert "knowledge, web, or memory answers" in prompt


def test_persona_chat_prompt_limits_ordinary_reply_length():
    from rag.persona_chat import PERSONA_PROMPT

    assert "30" in PERSONA_PROMPT.messages[0].prompt.template


def test_rag_generation_prompt_limits_answer_length():
    from rag.generate import PROMPT

    assert "300" in PROMPT.template


def test_web_worker_prompt_requires_structured_evidence_handoff():
    from agents.workflow import _worker_prompt

    context = PersonaAgentContext(
        persona_id="persona-a",
        workspace_id="local-default",
        knowledge_space_ids=("space-a",),
        conversation_id="thread-a",
        persona_name="Ames",
        persona_type="character",
    )

    prompt = _worker_prompt("web", context)

    assert "KEY FACTS" in prompt
    assert "SOURCES" in prompt
    assert "UNCERTAINTIES OR CONFLICTS" in prompt


def test_service_builds_one_parent_workflow(monkeypatch):
    import agents.service as service_module

    captured = {}
    parent_graph = object()
    monkeypatch.setattr(
        service_module,
        "build_persona_workflow",
        lambda model, checkpointer: captured.update(model=model, checkpointer=checkpointer) or parent_graph,
    )
    checkpointer = object()
    model = object()

    service = PersonaAgentService(checkpointer=checkpointer, model=model)

    assert service._graph() is parent_graph
    assert captured == {"model": model, "checkpointer": checkpointer}


def test_supervisor_handoff_returns_to_persona_response():
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver

    from agents.workflow import build_persona_workflow

    class ToolCallingFake(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    model = ToolCallingFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "delegate_to_web",
                        "args": {"request": "today's news"},
                        "id": "handoff-web",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Current public result."),
            AIMessage(content="Persona final response."),
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
        {"messages": [("user", "What happened today?")], "active_worker": None},
        {"configurable": {"thread_id": "persona-a:thread-a"}},
        context=context,
    )

    assert result["active_worker"] is None
    assert result["worker_results"] == [{"worker": "web", "summary": "Current public result."}]
    assert result["messages"][-1].content == "Persona final response."


def test_management_handoff_resumes_in_same_parent_workflow(db_session):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    from agents.workflow import build_persona_workflow

    class ToolCallingFake(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    persona = create_persona(db_session, "Alpha")
    db_session.commit()
    context = PersonaAgentContext(
        persona_id=persona.id,
        workspace_id="local-default",
        knowledge_space_ids=(persona.knowledge_space_id,),
        conversation_id="thread-a",
        persona_name=persona.name,
        persona_type=persona.persona_type,
        session_factory=lambda: db_session,
    )
    model = ToolCallingFake(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "delegate_to_management",
                        "args": {"request": "rename"},
                        "id": "handoff-management",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "rename_persona",
                        "args": {"name": "Beta"},
                        "id": "rename-persona",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="Persona renamed."),
            AIMessage(content="Persona final response."),
        ]
    )
    graph = build_persona_workflow(model, MemorySaver())
    config = {"configurable": {"thread_id": f"{persona.id}:thread-a"}}

    graph.invoke({"messages": [("user", "Rename yourself")], "active_worker": None}, config, context=context)

    assert graph.get_state(config).interrupts
    result = graph.invoke(Command(resume={"approved": True}), config, context=context)

    assert db_session.get(type(persona), persona.id).name == "Beta"
    assert result["messages"][-1].content == "Persona final response."


def test_registry_exposes_expected_read_only_tools():
    assert READ_ONLY_TOOL_NAMES == (
        "search_persona_knowledge",
        "web_search",
        "list_persona_documents",
        "read_persona_memories",
    )
    read_only_specs = [spec for spec in tool_specs() if spec.name in READ_ONLY_TOOL_NAMES]
    assert read_only_specs and all(not spec.requires_confirmation for spec in read_only_specs)


def test_supervisor_routes_to_capability_specialists():
    assert route_specialist("查一下今天的新闻") == "web"
    assert route_specialist("记住我喜欢红茶") == "memory"
    assert route_specialist("列出这个角色的资料") == "management"
    assert route_specialist("根据资料介绍她的经历") == "conversation"
    assert route_specialist("你好") == "conversation"


def test_every_specialist_receives_persona_type_and_profile():
    context = PersonaAgentContext(
        persona_id="persona-a",
        workspace_id="local-default",
        knowledge_space_ids=("space-a",),
        conversation_id="thread-a",
        persona_name="爱弥斯",
        persona_type="character",
        persona_profile={"voice": "活泼", "boundaries": "不伤害用户"},
    )

    for specialist in ("conversation", "web", "memory", "management"):
        prompt = specialist_prompt(specialist, context)
        assert "严格遵循人物设定" in prompt
        assert "活泼" in prompt
        assert "不伤害用户" in prompt


def test_all_specialists_share_one_conversation_thread():
    context = PersonaAgentContext(
        persona_id="persona-a",
        workspace_id="local-default",
        knowledge_space_ids=("space-a",),
        conversation_id="conversation-a",
        persona_name="Alpha",
        persona_type="character",
        persona_profile={},
    )

    assert PersonaAgentService.thread_id(context, "conversation") == PersonaAgentService.thread_id(
        context, "memory"
    )


def test_rag_tool_uses_only_server_injected_scope():
    captured = {}

    class FakeRagService:
        def query(self, request):
            captured["request"] = request
            return RagResult.empty("none")

    context = PersonaAgentContext(
        persona_id="persona-a",
        workspace_id="local-default",
        knowledge_space_ids=("space-a",),
        conversation_id="thread-a",
        persona_name="Alpha",
        persona_type="character",
        persona_profile={"description": "calm"},
    )

    run_persona_knowledge_search("private facts", context, FakeRagService())

    request = captured["request"]
    assert request.context.persona_id == "persona-a"
    assert request.context.knowledge_space_ids == ("space-a",)
    assert request.force_knowledge is True
    assert request.allow_web_fallback is False


def test_knowledge_tool_returns_fail_closed_specialist_result():
    class FakeRagService:
        def query(self, request):
            return RagResult(
                answer_draft="model guess must not reach supervisor",
                evidence=({"content": "weak evidence"},),
                confidence=0.2,
                used_web_search=False,
                trace=({"node": "quality_gate"},),
                grounded=False,
                useful=True,
                missing_points=("资料没有说明原因",),
            )

    context = PersonaAgentContext(
        persona_id="persona-a",
        workspace_id="local-default",
        knowledge_space_ids=("space-a",),
        conversation_id="thread-a",
        persona_name="Alpha",
        persona_type="character",
    )

    result = run_persona_knowledge_search("为什么", context, FakeRagService())

    assert result["specialist"] == "knowledge"
    assert result["status"] == "insufficient"
    assert result["answer"] == ""
    assert result["evidence"] == []
    assert result["uncertainties"] == ["资料没有说明原因"]


def test_knowledge_finalize_discards_worker_text_when_gate_rejects_evidence():
    from agents.workflow import _finalize_worker

    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "delegate_to_knowledge",
                    "args": {"request": "为什么"},
                    "id": "handoff-knowledge",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "specialist": "knowledge",
                    "status": "insufficient",
                    "answer": "",
                    "evidence": [],
                    "citations": [],
                    "uncertainties": ["资料没有说明原因"],
                    "trace": [{"node": "quality_gate"}],
                    "confidence": 0.2,
                }
            ),
            name="search_persona_knowledge",
            tool_call_id="rag-tool",
        ),
        AIMessage(content="她可能是为了自由，这句话没有证据。"),
    ]

    updates = _finalize_worker("knowledge")({"messages": messages})

    assert updates["worker_results"][0]["status"] == "insufficient"
    handoff = updates["messages"][0].content
    assert "她可能是为了自由" not in handoff
    assert "资料没有说明原因" in handoff


def test_knowledge_finalize_only_hands_accepted_answer_to_supervisor():
    from agents.workflow import _finalize_worker

    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "delegate_to_knowledge",
                    "args": {"request": "经历"},
                    "id": "handoff-knowledge",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {
                    "specialist": "knowledge",
                    "status": "accepted",
                    "answer": "她在十八岁时离开故乡。",
                    "evidence": [{"content": "十八岁时离开故乡", "filename": "设定.md"}],
                    "citations": [{"filename": "设定.md"}],
                    "uncertainties": [],
                    "trace": [{"node": "quality_gate"}],
                    "confidence": 0.9,
                }
            ),
            name="search_persona_knowledge",
            tool_call_id="rag-tool",
        ),
        AIMessage(content="自由发挥的 Worker 总结。"),
    ]

    updates = _finalize_worker("knowledge")({"messages": messages})

    handoff = updates["messages"][0].content
    assert "她在十八岁时离开故乡" in handoff
    assert "设定.md" in handoff
    assert "自由发挥的 Worker 总结" not in handoff


def test_document_tool_never_lists_another_personas_documents(db_session):
    first = create_persona(db_session, "First")
    second = create_persona(db_session, "Second")
    db_session.add_all(
        [
            DocumentJob(
                workspace_id="local-default",
                knowledge_space_id=first.knowledge_space_id,
                original_filename="first.md",
                markdown_filename="first.md",
                source_path="first.md",
                status="indexed",
            ),
            DocumentJob(
                workspace_id="local-default",
                knowledge_space_id=second.knowledge_space_id,
                original_filename="second.md",
                markdown_filename="second.md",
                source_path="second.md",
                status="indexed",
            ),
        ]
    )
    db_session.commit()
    context = PersonaAgentContext(
        persona_id=first.id,
        workspace_id="local-default",
        knowledge_space_ids=(first.knowledge_space_id,),
        conversation_id="thread-a",
        persona_name=first.name,
        persona_type="knowledge_expert",
        persona_profile={},
        session_factory=lambda: db_session,
    )

    documents = list_documents_for_context(context)

    assert [document["filename"] for document in documents] == ["first.md"]


def test_capability_question_does_not_match_character_ability_setting():
    assert is_capability_question("你有哪些 tools")
    assert is_capability_question("你会调用哪些工具")
    assert not is_capability_question("异能力>>长航的星辉")
    assert not is_capability_question("给你加上一些能力设定")


def test_supervisor_routes_profile_mutations_to_management():
    assert route_specialist("update_persona_profile") == "management"
    assert route_specialist("rename_persona") == "management"
    assert route_specialist("把你的名字改为 Ameath") == "management"
    assert route_specialist("给你加上一些设定：电子幽灵") == "management"
    assert route_specialist("记住，这是你的共鸣回路：光学取样") == "management"
    assert route_specialist("记住我喜欢红茶") == "memory"
