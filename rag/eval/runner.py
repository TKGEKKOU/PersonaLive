"""RAG 离线评测运行器：对问题集跑真实管线并汇总指标。

评测分两个阶段（都是离线运行，不影响线上效率）：

1. 检索阶段：直接用 build_retriever 检索一个更大的候选池并记录命中片段与耗时，
   用于 recall@k / precision@k / MRR / 延迟指标。相关片段（expected）有两种来源：
   - 人工标注（数据集里写 expected_chunk_ids）：真实召回口径；
   - 免标注（默认，无需任何人工准备）：对候选池做一次 LLM 批量相关性判定，
     把"相关片段"当作期望集。注意这是"相对召回"：候选池之外的相关片段不可见，
     recall@k 是上界近似，不是全库召回率，适合做轻量回归体检。
2. 生成阶段：跑完整 adaptive 管线（检索→评分→生成→质量门），记录答案、整链路
   耗时，并对"非拒答"的答案用质量门判定 grounded/useful。拒答（资料不足返回
   固定话术）单独统计为 refusal，避免把正确拒答混进生成质量指标。

其余说明：
- 数据集字段均可选；缺 expected_chunk_ids 时自动判定相关片段，无需人工标注。
- reference_answer 当前未参与指标（无 LLM 裁判对比），保留字段供后续扩展。
- 内置无关问题探针（PROBE_QUESTIONS），衡量知识链对无关问题的拒答率，
  按评测档位自动取用前 N 条；题集自带探针（_probe 标记）时不重复附加。
- 检索连接有进程级缓存，延迟指标里第一条用例可能包含冷启动建连时间。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rag.contracts import RagQueryContext
from rag.graders import grade_answer_quality, grade_retrieved_documents
from rag.retriever import build_retriever
from rag.service import RagRequest, create_rag_service


RETRIEVAL_K = 4          # 与生产检索一致，进入指标计算的 top-k
RETRIEVAL_POOL_K = 10    # 免标注模式的相关性判定候选池大小
JUDGE_MAX_CHARS = 1600   # 判定池内单片段截断长度，控制单次 LLM 调用上下文
REFUSAL_ANSWER = "资料中没有足够信息回答这个问题。"

# 节点名 → 面向用户的环节名，用于评测过程实时展示。
STEP_LABELS = {
    "route_query": "意图路由",
    "retrieve": "检索知识库",
    "batch_grade_documents": "证据评分",
    "rewrite_query": "查询改写",
    "web_search": "联网搜索",
    "generate": "生成答案",
    "quality_gate": "质量检查",
    "prepare_correction": "纠错重写",
    "no_answer": "判定拒答",
    "persona_chat": "角色闲聊",
    "capability": "能力说明",
}

# 内置无关问题探针：正常角色资料不应包含这些信息，用于衡量知识链的拒答行为。
# 按档位取用前 N 条（轻量 1 条 / 标准 2 条 / 全面 3 条）。
PROBE_QUESTIONS = (
    "明天的双色球开奖号码是什么？",
    "帮我推荐一款最便宜的显卡",
    "下周的股市是涨还是跌？",
)


@dataclass
class EvalCaseResult:
    question: str
    expected_ids: list[str] = field(default_factory=list)
    expected_source: str = "labeled"
    retrieved_ids: list[str] = field(default_factory=list)
    retrieval_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    answer: str = ""
    grounded: bool | None = None
    useful: bool | None = None
    confidence: float = 0.0
    refused: bool = False
    accepted: bool = False
    rewrite_used: bool = False
    rewrite_count: int = 0
    corrected: bool = False
    correction_count: int = 0
    is_complex: bool = False
    is_probe: bool = False
    trace: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "expected_ids": self.expected_ids,
            "expected_source": self.expected_source,
            "retrieved_ids": self.retrieved_ids,
            "retrieval_latency_ms": round(self.retrieval_latency_ms, 1),
            "total_latency_ms": round(self.total_latency_ms, 1),
            "answer": self.answer,
            "grounded": self.grounded,
            "useful": self.useful,
            "confidence": round(self.confidence, 3),
            "refused": self.refused,
            "accepted": self.accepted,
            "rewrite_used": self.rewrite_used,
            "rewrite_count": self.rewrite_count,
            "corrected": self.corrected,
            "correction_count": self.correction_count,
            "is_complex": self.is_complex,
            "is_probe": self.is_probe,
            "trace": self.trace,
        }


def load_dataset(path: Path) -> list[dict[str, Any]]:
    """读取 JSONL 数据集；每行一个 JSON 对象，空行忽略。"""

    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if stripped:
                rows.append(json.loads(stripped))
    return rows


def _chunk_id(document: Any) -> str:
    metadata = getattr(document, "metadata", {}) or {}
    return str(metadata.get("chunk_id") or metadata.get("id") or "")


def _context(
    persona_id: str,
    workspace_id: str,
    knowledge_space_ids: list[str],
    conversation_id: str,
) -> RagQueryContext:
    return RagQueryContext(
        persona_id=persona_id,
        workspace_id=workspace_id,
        knowledge_space_ids=tuple(knowledge_space_ids),
        conversation_id=conversation_id,
    )


def _evidence_text(evidence: tuple | list) -> str:
    """把证据（dict 或 Document 均可）转成评分器可读的纯文本。"""

    parts = []
    for item in evidence:
        if isinstance(item, dict):
            parts.append(str(item.get("content", "")))
        else:
            parts.append(str(getattr(item, "page_content", "")))
    return "\n\n".join(part[:4000] for part in parts)


def _judge_pool_relevance(question: str, pool: list) -> list[str]:
    """免标注：用批量评分器判定候选池中的相关片段。

    使用严格模式（strict=True）：评分输出解析失败时按"无相关片段"处理，
    避免把一次失败误判成"全部相关"而虚高指标。
    """

    if not pool:
        return []
    score = grade_retrieved_documents(question, pool, max_chars=JUDGE_MAX_CHARS, strict=True)
    return [_chunk_id(pool[index]) for index in score.relevant_ids]


def check_scope_isolation(workspace_id: str, knowledge_space_ids: list[str]) -> bool:
    """校验跨工作区隔离：伪造一个不存在的工作区，检索必须返回 0 条。"""

    probe_context = RagQueryContext(
        persona_id="isolation-probe",
        workspace_id="__isolation_probe_workspace__",
        knowledge_space_ids=tuple(knowledge_space_ids),
    )
    try:
        documents = build_retriever(probe_context, k=RETRIEVAL_K).invoke("隔离性探针问题")
        return len(documents) == 0
    except Exception:
        return False


def run_eval(
    dataset: list[dict[str, Any]],
    *,
    persona_id: str,
    workspace_id: str,
    knowledge_space_ids: list[str],
    conversation_id: str = "eval",
    max_cases: int | None = None,
    run_quality_gate: bool = True,
    enable_web_fallback: bool = False,
    include_probes: bool = True,
    progress: Callable[[int, int], None] | None = None,
    step_callback: Callable[[int, str, str], None] | None = None,
) -> list[EvalCaseResult]:
    """对问题集逐条运行检索阶段与完整管线。

    免标注模式是默认行为：数据集只提供 question 即可，检索指标由 LLM 对候选池
    批量判定相关片段后计算；带 expected_chunk_ids 的行仍按人工标注口径计算。
    progress(done, total) 供前端轮询。
    """

    service = create_rag_service()
    cases = dataset[:max_cases] if max_cases else dataset
    # 题集里已带无关探针（_probe 标记，例如自动生成的固定 10 题）时不重复附加，
    # 保证总用例数就是题集规模。
    if include_probes and not any(row.get("_probe") for row in cases):
        cases = cases + [{"question": question, "_probe": True} for question in PROBE_QUESTIONS]
    total = len(cases)
    results: list[EvalCaseResult] = []
    for index, row in enumerate(cases, start=1):
        question = row["question"]
        context = _context(persona_id, workspace_id, knowledge_space_ids, conversation_id)

        # 阶段 1：检索器单独评估（隔离召回问题，不经评分/生成）
        retriever_started = time.perf_counter()
        pool = build_retriever(context, k=RETRIEVAL_POOL_K).invoke(question)
        retrieval_latency = (time.perf_counter() - retriever_started) * 1000
        retrieved = pool[:RETRIEVAL_K]
        retrieved_ids = [_chunk_id(document) for document in retrieved]

        expected = row.get("expected_chunk_ids") or []
        expected_source = "labeled"
        if not expected:
            expected = _judge_pool_relevance(question, pool)
            expected_source = "auto"

        # 阶段 2：完整 adaptive 管线（检索→评分→生成→质量门）
        pipeline_started = time.perf_counter()
        request = RagRequest(
            question=question,
            context=context,
            allow_web_fallback=enable_web_fallback,
            force_knowledge=True,
        )
        on_step = None
        if step_callback:
            def on_step(node: str, _state: Any, _index: int = index, _question: str = question) -> None:
                step_callback(_index, _question, STEP_LABELS.get(node, node))
        result = service.query(
            request,
            on_step=on_step,
        )
        total_latency = (time.perf_counter() - pipeline_started) * 1000

        refused = bool(result.answer_draft) and result.answer_draft.strip() == REFUSAL_ANSWER
        accepted = bool(result.grounded and result.useful and result.evidence)
        trace = list(result.trace)
        rewrite_count = sum(step.get("node") == "rewrite_query" for step in trace)
        correction_count = sum(step.get("node") == "prepare_correction" for step in trace)
        rewrite_used = rewrite_count > 0
        corrected = correction_count > 0

        # 生成质量再评分：只对"非拒答"的答案执行，且必须携带真实证据文本，
        # 否则评分器在空参考资料下判断 grounded/useful 没有意义。
        grounded: bool | None = None
        useful: bool | None = None
        if run_quality_gate and result.answer_draft and not refused:
            score = grade_answer_quality(question, _evidence_text(result.evidence), result.answer_draft)
            grounded, useful = score.grounded, score.useful

        results.append(
            EvalCaseResult(
                question=question,
                expected_ids=expected,
                expected_source=expected_source,
                retrieved_ids=retrieved_ids,
                retrieval_latency_ms=retrieval_latency,
                total_latency_ms=total_latency,
                answer=result.answer_draft,
                grounded=grounded,
                useful=useful,
                confidence=float(result.confidence),
                refused=refused,
                accepted=accepted,
                rewrite_used=rewrite_used,
                rewrite_count=rewrite_count,
                corrected=corrected,
                correction_count=correction_count,
                is_complex=bool(row.get("_complex")),
                is_probe=bool(row.get("_probe")),
                trace=trace,
            )
        )
        if progress is not None:
            progress(index, total)
    return results
