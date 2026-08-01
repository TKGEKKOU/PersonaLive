"""RAG 离线评测运行器：对标注问题集跑真实管线并汇总指标。

评测分两个阶段（都是离线运行，不影响线上效率）：

1. 检索阶段：直接用 build_retriever 检索并记录命中片段与耗时，
   用于 recall@k / precision@k / MRR / 延迟指标——这一步隔离了检索器，
   不经过评分/生成，能定位"召回"问题。
2. 生成阶段：跑完整 adaptive 管线（检索→评分→生成→质量门），
   记录答案、整链路耗时，并复用质量门的 grounded/useful 判定作为生成质量指标。

标注字段均可选：缺 expected_chunk_ids 时跳过检索指标；缺 reference_answer
时跳过正确性判断（当前版本未内置 LLM 裁判，可后续扩展）。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rag.contracts import RagQueryContext
from rag.graders import grade_answer_quality
from rag.retriever import build_retriever
from rag.service import RagRequest, create_rag_service


@dataclass
class EvalCaseResult:
    question: str
    expected_ids: list[str] = field(default_factory=list)
    retrieved_ids: list[str] = field(default_factory=list)
    retrieval_latency_ms: float = 0.0
    total_latency_ms: float = 0.0
    answer: str = ""
    grounded: bool | None = None
    useful: bool | None = None
    trace: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "question": self.question,
            "expected_ids": self.expected_ids,
            "retrieved_ids": self.retrieved_ids,
            "retrieval_latency_ms": round(self.retrieval_latency_ms, 1),
            "total_latency_ms": round(self.total_latency_ms, 1),
            "answer": self.answer,
            "grounded": self.grounded,
            "useful": self.useful,
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


def run_eval(
    dataset: list[dict[str, Any]],
    *,
    persona_id: str,
    workspace_id: str,
    knowledge_space_ids: list[str],
    conversation_id: str = "eval",
    max_cases: int | None = None,
    run_quality_gate: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> list[EvalCaseResult]:
    """对数据集逐条运行检索阶段与完整管线；progress(done, total) 供前端轮询。"""

    service = create_rag_service()
    cases = dataset[:max_cases] if max_cases else dataset
    total = len(cases)
    results: list[EvalCaseResult] = []
    for index, row in enumerate(cases, start=1):
        question = row["question"]
        context = _context(persona_id, workspace_id, knowledge_space_ids, conversation_id)

        # 阶段 1：检索器单独评估（隔离召回问题，不经评分/生成）
        retriever_started = time.perf_counter()
        retrieved = build_retriever(context, k=4).invoke(question)
        retrieval_latency = (time.perf_counter() - retriever_started) * 1000
        retrieved_ids = [_chunk_id(document) for document in retrieved]

        # 阶段 2：完整 adaptive 管线（检索→评分→生成→质量门）
        pipeline_started = time.perf_counter()
        result = service.query(
            RagRequest(question=question, context=context, force_knowledge=True)
        )
        total_latency = (time.perf_counter() - pipeline_started) * 1000

        grounded: bool | None = None
        useful: bool | None = None
        if run_quality_gate and result.answer_draft:
            score = grade_answer_quality(
                question,
                "\n\n".join(
                    str(getattr(document, "page_content", ""))[:4000]
                    for document in result.evidence
                ),
                result.answer_draft,
            )
            grounded, useful = score.grounded, score.useful

        results.append(
            EvalCaseResult(
                question=question,
                expected_ids=row.get("expected_chunk_ids") or [],
                retrieved_ids=retrieved_ids,
                retrieval_latency_ms=retrieval_latency,
                total_latency_ms=total_latency,
                answer=result.answer_draft,
                grounded=grounded,
                useful=useful,
                trace=list(result.trace),
            )
        )
        if progress is not None:
            progress(index, total)
    return results
