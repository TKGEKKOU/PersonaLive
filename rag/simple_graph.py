from typing import Any, Callable

from langgraph.constants import END, START
from langgraph.graph import StateGraph
from typing_extensions import TypedDict

from rag.contracts import RagQueryContext
from rag.generate import generate_answer
from rag.retriever import build_retriever
from rag.service import RagRequest, RagResult


class SimpleRagState(TypedDict, total=False):
    question: str
    context: RagQueryContext
    documents: list
    answer: str
    persona_name: str
    persona_profile: dict


def retrieve_node(state: SimpleRagState) -> SimpleRagState:
    documents = build_retriever(state["context"], k=4).invoke(state["question"])
    return {**state, "documents": documents}


def generate_node(state: SimpleRagState) -> SimpleRagState:
    answer = generate_answer(
        state["question"],
        state.get("documents", []),
        persona_name=state.get("persona_name", "角色"),
        persona_profile=state.get("persona_profile") or {},
    )
    return {**state, "answer": answer}


def build_graph():
    workflow = StateGraph(SimpleRagState)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("generate", generate_node)
    workflow.add_edge(START, "retrieve")
    workflow.add_edge("retrieve", "generate")
    workflow.add_edge("generate", END)
    return workflow.compile()


graph = build_graph()


def serialize_document(document: Any) -> dict:
    return {
        "content": (getattr(document, "page_content", "") or "")[:800],
        **dict(getattr(document, "metadata", {}) or {}),
    }


def run_simple(request: RagRequest, on_step: Callable | None = None) -> RagResult:
    del on_step  # simple 管线不提供分步回调，接口保持对齐
    state = graph.invoke(
        {
            "question": request.question,
            "context": request.context,
            "persona_name": request.persona_name,
            "persona_profile": request.persona_profile or {},
        }
    )
    documents = state.get("documents") or []
    return RagResult(
        answer_draft=state.get("answer", ""),
        evidence=tuple(serialize_document(document) for document in documents),
        confidence=1.0 if documents else 0.0,
        used_web_search=False,
        trace=(
            {"node": "retrieve", "document_count": len(documents), "confidence": None, "has_answer": False},
            {"node": "generate", "document_count": len(documents), "confidence": None, "has_answer": bool(state.get("answer"))},
        ),
        grounded=False,
        useful=bool(state.get("answer")),
        missing_points=(),
        interaction_mode="knowledge",
    )
