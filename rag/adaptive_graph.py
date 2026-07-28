from typing import Any

from langgraph.constants import END, START
from langgraph.graph import StateGraph
from typing_extensions import TypedDict

from settings import Settings
from rag.contracts import RagQueryContext
from rag.generate import format_documents, generate_answer
from rag.graders import grade_answer_quality, grade_retrieved_documents
from rag.query_rewriter import rewrite_query
from rag.interaction_router import route_interaction
from rag.persona_chat import describe_capabilities, generate_persona_reply
from rag.retriever import build_retriever
from rag.service import RagRequest, RagResult
from rag.web_search import web_search_documents


settings = Settings.load()


class AdaptiveRagState(TypedDict, total=False):
    question: str
    query: str
    context: RagQueryContext
    allow_web_fallback: bool
    datasource: str
    documents: list
    answer: str
    previous_answer: str
    correction_feedback: str
    confidence: float
    confidence_reason: str
    needs_quality_check: bool
    rewrite_count: int
    generation_retry_count: int
    used_web_search: bool
    grounded: bool
    useful: bool
    missing_points: list[str]
    unsupported_claims: list[str]
    correction_action: str
    no_answer_reason: str
    trace: list[dict]
    persona_name: str
    persona_profile: dict
    available_tools: tuple[str, ...]
    interaction_mode: str
    force_knowledge: bool


def _complete(
    state: AdaptiveRagState,
    node_name: str,
    **updates: Any,
) -> AdaptiveRagState:
    completed = {**state, **updates}
    trace = list(state.get("trace", []))
    trace.append(
        {
            "node": node_name,
            "document_count": len(completed.get("documents") or []),
            "confidence": completed.get("confidence"),
            "has_answer": bool(completed.get("answer")),
        }
    )
    completed["trace"] = trace
    return completed


def route_query_node(state: AdaptiveRagState) -> AdaptiveRagState:
    decision = (
        "knowledge"
        if state.get("force_knowledge", False)
        else route_interaction(state["question"], bool(state.get("allow_web_fallback", False)))
    )
    datasource = "web_search" if decision == "web" else "vectorstore"
    return _complete(state, "route_query", datasource=datasource, interaction_mode=decision)


def decide_route(state: dict) -> str:
    mode = state.get("interaction_mode", "knowledge")
    if mode in {"conversation", "capability"}:
        return mode
    return "web_search" if state.get("datasource") == "web_search" else "vectorstore"


def persona_chat_node(state: AdaptiveRagState) -> AdaptiveRagState:
    answer = generate_persona_reply(
        state.get("persona_name", ""),
        state.get("persona_profile") or {},
        state["question"],
    )
    return _complete(state, "persona_chat", answer=answer, useful=True)


def capability_node(state: AdaptiveRagState) -> AdaptiveRagState:
    answer = describe_capabilities(tuple(state.get("available_tools") or ()))
    return _complete(state, "capability", answer=answer, useful=True)


def retrieve_node(state: AdaptiveRagState) -> AdaptiveRagState:
    query = state.get("query") or state["question"]
    documents = build_retriever(state["context"], k=4).invoke(query)
    return _complete(
        state,
        "retrieve",
        query=query,
        documents=documents,
        confidence=0.0,
        confidence_reason="",
        needs_quality_check=True,
    )


def batch_grade_documents_node(state: AdaptiveRagState) -> AdaptiveRagState:
    documents = state.get("documents", [])
    score = grade_retrieved_documents(state["question"], documents)
    filtered = [documents[index] for index in score.relevant_ids]
    return _complete(
        state,
        "batch_grade_documents",
        documents=filtered,
        confidence=score.confidence,
        confidence_reason=score.reason,
        needs_quality_check=score.confidence < settings.confidence_threshold,
    )


def decide_after_batch_grade(
    state: dict,
    max_rewrite_count: int | None = None,
    enable_web_fallback: bool | None = None,
) -> str:
    rewrite_limit = settings.max_rewrite_count if max_rewrite_count is None else max_rewrite_count
    web_enabled = bool(state.get("allow_web_fallback", False)) if enable_web_fallback is None else enable_web_fallback
    if state.get("documents"):
        return "generate"
    if int(state.get("rewrite_count", 0)) < rewrite_limit:
        return "rewrite_query"
    if web_enabled and not state.get("used_web_search", False):
        return "web_search"
    return "no_answer"


def rewrite_query_node(state: AdaptiveRagState) -> AdaptiveRagState:
    rewritten = rewrite_query(state.get("query") or state["question"])
    return _complete(
        state,
        "rewrite_query",
        query=rewritten,
        documents=[],
        answer="",
        rewrite_count=int(state.get("rewrite_count", 0)) + 1,
        needs_quality_check=True,
    )


def web_search_node(state: AdaptiveRagState) -> AdaptiveRagState:
    documents = web_search_documents(
        state.get("query") or state["question"],
        recent=state.get("datasource") == "web_search",
    )
    return _complete(
        state,
        "web_search",
        documents=documents,
        used_web_search=True,
        confidence=0.0,
        confidence_reason="Web results require answer quality checking.",
        needs_quality_check=True,
    )


def decide_after_web_search(state: dict) -> str:
    return "generate" if state.get("documents") else "no_answer"


def generate_node(state: AdaptiveRagState) -> AdaptiveRagState:
    answer = generate_answer(
        state["question"],
        state.get("documents", []),
        previous_answer=state.get("previous_answer", ""),
        correction_feedback=state.get("correction_feedback", ""),
        persona_name=state.get("persona_name", "角色"),
        persona_profile=state.get("persona_profile") or {},
    )
    return _complete(state, "generate", answer=answer, correction_feedback="")


def decide_after_generation(state: dict) -> str:
    return "quality_gate" if state.get("needs_quality_check", True) else "useful"


def quality_gate_node(state: AdaptiveRagState) -> AdaptiveRagState:
    score = grade_answer_quality(
        state["question"],
        format_documents(state.get("documents", [])),
        state.get("answer", ""),
    )
    return _complete(
        state,
        "quality_gate",
        grounded=score.grounded,
        useful=score.useful,
        missing_points=score.missing_points,
        unsupported_claims=score.unsupported_claims,
        correction_action=score.correction_action,
    )


def decide_quality(
    state: dict,
    max_generation_retry: int | None = None,
    max_rewrite_count: int | None = None,
    enable_web_fallback: bool | None = None,
) -> str:
    generation_limit = settings.max_generation_retry if max_generation_retry is None else max_generation_retry
    rewrite_limit = settings.max_rewrite_count if max_rewrite_count is None else max_rewrite_count
    web_enabled = bool(state.get("allow_web_fallback", False)) if enable_web_fallback is None else enable_web_fallback
    if state.get("grounded") is True and state.get("useful") is True:
        return "useful"
    action = state.get("correction_action", "regenerate")
    if action == "no_answer":
        return "no_answer"
    if action == "web_search" and web_enabled and not state.get("used_web_search", False):
        return "web_search"
    if action == "retrieve_again" and int(state.get("rewrite_count", 0)) < rewrite_limit:
        return "rewrite_query"
    if int(state.get("generation_retry_count", 0)) < generation_limit:
        return "prepare_correction"
    return "no_answer"


def prepare_correction_node(state: AdaptiveRagState) -> AdaptiveRagState:
    missing = "；".join(state.get("missing_points", [])) or "无"
    unsupported = "；".join(state.get("unsupported_claims", [])) or "无"
    return _complete(
        state,
        "prepare_correction",
        previous_answer=state.get("answer", ""),
        correction_feedback=f"缺失点：{missing}\n无证据结论：{unsupported}",
        generation_retry_count=int(state.get("generation_retry_count", 0)) + 1,
        needs_quality_check=True,
    )


def no_answer_node(state: AdaptiveRagState) -> AdaptiveRagState:
    reason = (
        "Web search and local knowledge did not provide enough evidence."
        if state.get("used_web_search")
        else "Local knowledge did not provide enough evidence."
    )
    return _complete(
        state,
        "no_answer",
        answer="资料中没有足够信息回答这个问题。",
        no_answer_reason=reason,
        grounded=False,
        useful=False,
        missing_points=[reason],
    )


def build_graph():
    workflow = StateGraph(AdaptiveRagState)
    for name, node in (
        ("route_query", route_query_node),
        ("persona_chat", persona_chat_node),
        ("capability", capability_node),
        ("retrieve", retrieve_node),
        ("batch_grade_documents", batch_grade_documents_node),
        ("rewrite_query", rewrite_query_node),
        ("web_search", web_search_node),
        ("generate", generate_node),
        ("quality_gate", quality_gate_node),
        ("prepare_correction", prepare_correction_node),
        ("no_answer", no_answer_node),
    ):
        workflow.add_node(name, node)
    workflow.add_edge(START, "route_query")
    workflow.add_conditional_edges(
        "route_query",
        decide_route,
        {
            "conversation": "persona_chat",
            "capability": "capability",
            "vectorstore": "retrieve",
            "web_search": "web_search",
        },
    )
    workflow.add_edge("persona_chat", END)
    workflow.add_edge("capability", END)
    workflow.add_edge("retrieve", "batch_grade_documents")
    workflow.add_conditional_edges(
        "batch_grade_documents",
        decide_after_batch_grade,
        {"generate": "generate", "rewrite_query": "rewrite_query", "web_search": "web_search", "no_answer": "no_answer"},
    )
    workflow.add_edge("rewrite_query", "retrieve")
    workflow.add_conditional_edges("web_search", decide_after_web_search, {"generate": "generate", "no_answer": "no_answer"})
    workflow.add_conditional_edges("generate", decide_after_generation, {"quality_gate": "quality_gate", "useful": END})
    workflow.add_conditional_edges(
        "quality_gate",
        decide_quality,
        {"useful": END, "prepare_correction": "prepare_correction", "rewrite_query": "rewrite_query", "web_search": "web_search", "no_answer": "no_answer"},
    )
    workflow.add_edge("prepare_correction", "generate")
    workflow.add_edge("no_answer", END)
    return workflow.compile()


graph = build_graph()


def serialize_document(document: Any) -> dict:
    return {
        "content": (getattr(document, "page_content", "") or "")[:800],
        **dict(getattr(document, "metadata", {}) or {}),
    }


def run_adaptive(request: RagRequest) -> RagResult:
    state = graph.invoke(
        {
            "question": request.question,
            "query": request.question,
            "context": request.context,
            "allow_web_fallback": request.allow_web_fallback,
            "rewrite_count": 0,
            "generation_retry_count": 0,
            "used_web_search": False,
            "trace": [],
            "persona_name": request.persona_name,
            "persona_profile": request.persona_profile or {},
            "available_tools": request.available_tools,
            "interaction_mode": "knowledge",
            "force_knowledge": request.force_knowledge,
        }
    )
    documents = state.get("documents") or []
    return RagResult(
        answer_draft=state.get("answer", ""),
        evidence=tuple(serialize_document(document) for document in documents),
        confidence=float(state.get("confidence", 0.0)),
        used_web_search=bool(state.get("used_web_search", False)),
        trace=tuple(state.get("trace", [])),
        grounded=bool(state.get("grounded", False)),
        useful=bool(state.get("useful", False)),
        missing_points=tuple(state.get("missing_points", [])),
        interaction_mode=state.get("interaction_mode", "knowledge"),
    )
