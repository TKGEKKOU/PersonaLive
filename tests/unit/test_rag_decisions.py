import pytest

from rag.contracts import RagQueryContext


@pytest.fixture
def rag_context():
    return RagQueryContext("persona-a", "local-default", ("space-a",))


def test_retrieve_node_passes_server_context(monkeypatch, rag_context):
    from rag.adaptive_graph import retrieve_node

    captured = {}

    class FakeRetriever:
        def invoke(self, query):
            captured["query"] = query
            return []

    monkeypatch.setattr(
        "rag.adaptive_graph.build_retriever",
        lambda context, k=4: captured.update(context=context) or FakeRetriever(),
    )
    state = retrieve_node(
        {"question": "facts", "query": "facts", "context": rag_context}
    )

    assert captured == {"context": rag_context, "query": "facts"}
    assert state["documents"] == []


def test_empty_evidence_rewrites_then_uses_web_fallback():
    from rag.adaptive_graph import decide_after_batch_grade

    assert decide_after_batch_grade({"documents": [object()]}, 2, True) == "generate"
    assert decide_after_batch_grade({"documents": [], "rewrite_count": 0}, 2, True) == "rewrite_query"
    assert decide_after_batch_grade({"documents": [], "rewrite_count": 2}, 2, True) == "web_search"
    assert decide_after_batch_grade(
        {"documents": [], "rewrite_count": 2, "used_web_search": True}, 2, True
    ) == "no_answer"


def test_quality_failure_has_bounded_retry_and_feedback():
    from rag.adaptive_graph import decide_quality, prepare_correction_node

    state = {
        "grounded": False,
        "useful": False,
        "correction_action": "regenerate",
        "generation_retry_count": 0,
        "missing_points": ["时间范围"],
        "unsupported_claims": ["未经资料支持的结论"],
        "answer": "old",
    }
    assert decide_quality(state, max_generation_retry=1) == "prepare_correction"
    corrected = prepare_correction_node(state)
    assert corrected["generation_retry_count"] == 1
    assert "时间范围" in corrected["correction_feedback"]
    assert "未经资料支持的结论" in corrected["correction_feedback"]
    assert decide_quality(corrected, max_generation_retry=1) == "no_answer"


def test_high_confidence_answer_uses_lightweight_quality_gate(monkeypatch):
    from langchain_core.documents import Document
    from rag.adaptive_graph import quality_gate_node

    monkeypatch.setattr(
        "rag.adaptive_graph.grade_answer_quality",
        lambda *args: (_ for _ in ()).throw(AssertionError("high confidence must not call LLM grader")),
    )

    result = quality_gate_node(
        {
            "question": "格式要求是什么",
            "documents": [Document(page_content="正文小四，1.3倍行距")],
            "answer": "正文使用小四，行距为1.3倍。",
            "confidence": 0.9,
        }
    )

    assert result["grounded"] is True
    assert result["useful"] is True
    assert result["correction_action"] == "no_answer"


def test_interaction_router_keeps_chat_and_capabilities_out_of_rag():
    from rag.interaction_router import route_interaction

    assert route_interaction("你好", True) == "conversation"
    assert route_interaction("你会调用哪些 tools", True) == "capability"
    assert route_interaction("根据资料说明她的经历", True) == "knowledge"
    assert route_interaction("今天北京天气", True) == "web"


def test_force_knowledge_bypasses_conversation_routing(rag_context):
    from rag.adaptive_graph import route_query_node

    state = route_query_node(
        {"question": "你好", "context": rag_context, "force_knowledge": True}
    )

    assert state["interaction_mode"] == "knowledge"
    assert state["datasource"] == "vectorstore"


def test_greeting_uses_persona_without_retrieval(monkeypatch, rag_context):
    from rag.adaptive_graph import run_adaptive
    from rag.service import RagRequest

    monkeypatch.setattr(
        "rag.adaptive_graph.generate_persona_reply",
        lambda name, profile, question: f"{name}:{profile['description']}:{question}",
    )
    monkeypatch.setattr(
        "rag.adaptive_graph.build_retriever",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not retrieve")),
    )

    result = run_adaptive(
        RagRequest(
            "你好",
            rag_context,
            persona_name="爱弥斯",
            persona_profile={"description": "活泼俏皮"},
        )
    )

    assert result.answer_draft == "爱弥斯:活泼俏皮:你好"
    assert result.interaction_mode == "conversation"
    assert [step["node"] for step in result.trace] == ["route_query", "persona_chat"]


def test_capability_question_reports_actual_empty_tool_manifest(rag_context):
    from rag.adaptive_graph import run_adaptive
    from rag.service import RagRequest

    result = run_adaptive(RagRequest("你会调用哪些tools", rag_context, persona_name="爱弥斯"))

    assert "没有已启用的工具" in result.answer_draft
    assert result.interaction_mode == "capability"
    assert result.evidence == ()


def test_rag_generation_always_receives_persona_profile(monkeypatch):
    from rag import generate

    captured = {}
    monkeypatch.setattr(
        generate,
        "_invoke_generation",
        lambda prompt, values: captured.update(values) or "answer",
    )

    answer = generate.generate_answer(
        "她有什么经历？",
        [],
        persona_name="爱弥斯",
        persona_profile={"description": "活泼俏皮的数据幽灵"},
    )

    assert answer == "answer"
    assert captured["persona_name"] == "爱弥斯"
    assert "活泼俏皮" in captured["persona_profile"]
