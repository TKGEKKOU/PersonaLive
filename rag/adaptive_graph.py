"""Adaptive/Corrective RAG 状态机。

主路径为路由 -> 检索 -> 批量证据评分 -> 生成 -> 质量门；证据不足时按上限执行
查询改写、联网回退或答案纠错，超过边界后返回保守的无答案结果。
"""

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


# ===== RAG 主流程（Adaptive / Corrective RAG）=====
#
#   route_query ─► retrieve ─► batch_grade_documents ─► generate ─► quality_gate ─► 结束
#                     │              │                    ▲              │
#                     ▼              ▼                    │              ▼
#              （无候选）      （无相关片段）         correction_feedback   regenerate /
#              rewrite_query ─►             │              ▲        retrieve_again /
#                                           └──────────────┘        web_search / no_answer
#
# 设计要点：
# 1. 证据先"批量化评分"再生成：一次 LLM 调用对全部候选片段打标（相关/不相关 +
#    confidence），避免逐片段调用放大成本，同时滤掉仅有词面重合的干扰片段。
# 2. 生成结果统一过"质量门"：只有 grounded（事实接地点）与 useful（解决问题）
#    同时为真才放行；confidence 达到阈值时跳过 LLM 门检，直接按高置信通过。
# 3. 纠错回路有硬边界：rewrite_count / generation_retry_count / web 兜底都受
#    配置限制，模型无法制造无限循环；最终兜底是保守的 no_answer。
# 4. trace 只记录可公开的摘要（节点名、片段数、置信度、是否有答案），不落 prompt。
class AdaptiveRagState(TypedDict, total=False):
    """图节点共享状态；计数器限制循环，trace 仅记录可公开的运行摘要。"""

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
    # 节点返回完整状态并追加运维 trace，不记录 prompt 或模型思维过程。
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
    # Agent 的 knowledge Worker 已经完成意图路由时可强制走知识链，避免在 RAG
    # 内部再次把同一问题误分到闲聊分支。
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


# 检索节点：以 query（可能是改写后的）发起 Dense+BM25 RRF 混合检索，k=4。
# 作用域过滤在 Milvus 服务端下推（见 rag/retriever.py），先过滤再排序。
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


# 批量证据评分：对全部候选片段一次打分（相关/不相关 + 整体置信度），
# 只保留 relevant_ids 指向的片段；confidence 低于阈值时置 needs_quality_check，
# 让质量门在生成后做 LLM 级校验（高置信则跳过，省一次 LLM 调用）。
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


# 候选为空时的退避顺序（固定）：
#   有效片段 → generate；否则改写重试（受限）→ 联网兜底（受限）→ 保守拒答。
# 先改写而不是直接联网，优先利用本地知识库。
def decide_after_batch_grade(
    state: dict,
    max_rewrite_count: int | None = None,
    enable_web_fallback: bool | None = None,
) -> str:
    rewrite_limit = settings.max_rewrite_count if max_rewrite_count is None else max_rewrite_count
    web_enabled = bool(state.get("allow_web_fallback", False)) if enable_web_fallback is None else enable_web_fallback
    # 回退顺序固定为：有效本地证据 -> 有界改写 -> 一次联网 -> 保守拒答。
    if state.get("documents"):
        return "generate"
    if int(state.get("rewrite_count", 0)) < rewrite_limit:
        return "rewrite_query"
    if web_enabled and not state.get("used_web_search", False):
        return "web_search"
    return "no_answer"


# 查询改写：把口语化问题改写成更适合向量/关键词检索的短查询；
# 每次改写都清空旧证据，避免脏证据参与下一轮评分。次数受 max_rewrite_count 限制。
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


# 联网兜底：仅在 allow_web_fallback 且本轮尚未用过时触发（见 decide_* 路由），
# 结果同样要过批量评分与质量门，不因其来源是网络而降低校验标准。
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


# 生成节点：结合证据与上一轮质量门的纠错反馈生成答案；
# 只接收可操作的 missing_points / unsupported_claims，不携带隐藏推理。
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
    # 所有生成结果都经过门禁；高置信度时门禁节点只做本地检查。
    return "quality_gate"


# 质量门：高置信度（≥ confidence_threshold）且有证据时直接放行（省 LLM 调用）；
# 否则调用 LLM 检查 grounded/useful，并让模型给出 correction_action，
# 该动作仍受外层计数器与 web 开关约束（见 decide_quality）。
def quality_gate_node(state: AdaptiveRagState) -> AdaptiveRagState:
    documents = state.get("documents", [])
    answer = (state.get("answer") or "").strip()
    if float(state.get("confidence") or 0.0) >= settings.confidence_threshold and documents and answer:
        return _complete(
            state,
            "quality_gate",
            grounded=True,
            useful=True,
            missing_points=[],
            unsupported_claims=[],
            correction_action="no_answer",
        )
    score = grade_answer_quality(
        state["question"],
        format_documents(documents),
        answer,
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


# 纠错路由：grounded 与 useful 同时为真才结束；否则按 correction_action
# 决定走 web_search / rewrite_query / prepare_correction（重生成）/ no_answer，
# 每一路都有次数上限，杜绝无限循环。
def decide_quality(
    state: dict,
    max_generation_retry: int | None = None,
    max_rewrite_count: int | None = None,
    enable_web_fallback: bool | None = None,
) -> str:
    generation_limit = settings.max_generation_retry if max_generation_retry is None else max_generation_retry
    rewrite_limit = settings.max_rewrite_count if max_rewrite_count is None else max_rewrite_count
    web_enabled = bool(state.get("allow_web_fallback", False)) if enable_web_fallback is None else enable_web_fallback
    # 只有“事实接地”和“解决问题”同时通过才能结束；评分器建议的纠错动作
    # 仍受本地计数器和联网开关约束，模型不能制造无限循环。
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


# 纠错反馈：把上一轮答案与"缺失点/无证据结论"汇总成下一轮生成的校正输入，
# 只传递可操作信息，避免模型依据自己的旧草稿自证。
def prepare_correction_node(state: AdaptiveRagState) -> AdaptiveRagState:
    # 只把可操作的缺失点和无证据结论反馈给下一轮生成，不传递隐藏推理。
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
    """声明节点和有界回边；所有循环最终都受 rewrite/retry 计数器终止。"""

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
    # 请求中的 context 已由服务端根据 persona 派生，浏览器和模型不能扩大知识范围。
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
