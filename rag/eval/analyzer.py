"""评测结果 AI 分析：从指标与逐条数据中提炼异常、性能与功能结论。

分析聚焦四件事：
1. 异常检测：可答问题检索指标、拒答率、置信度与 grounded/useful 是否矛盾；
2. 性能：检索与整链路延迟是否异常（含离群值）；
3. 功能：检索、生成、拒答、查询改写/纠错各环节是否按预期工作；
4. 建议：按优先级给出可执行改进。

字数严格限制在 MAX_ANALYSIS_CHARS（200 字）以内，由提示词与后处理双重约束。
"""

from __future__ import annotations

from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from rag.llm import get_llm


MAX_ANALYSIS_CHARS = 200

ANALYSIS_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是 RAG 评测分析助手。基于给定评测数据输出简短分析：第一句给总评；"
            "然后列出异常点（最多 3 条，每条不超过 40 字，无明显异常就写「无明显异常」）；"
            "最后给出最多 2 条改进建议（每条不超过 40 字）。"
            "总字数严格不超过 {limit} 字。用中文，不要 Markdown，不要列表符号。",
        ),
        ("human", "评测数据：\n{data}"),
    ]
)


def _fmt_rate(value: Any) -> str:
    if value is None:
        return "-"
    return f"{round(float(value) * 100)}%"


def _fmt_num(value: Any) -> str:
    if value is None:
        return "-"
    number = float(value)
    return str(int(number)) if number == int(number) else f"{number:.2f}"


def build_summary_text(metrics: dict, cases: list[dict]) -> str:
    """把指标与逐条结果压成紧凑文本，作为分析器的输入。"""

    m = metrics
    lines = [
        f"用例总数:{m.get('cases_total', 0)} 可答数:{m.get('cases_answerable', 0)}",
        (
            "可答召回:" + _fmt_rate(m.get("recall_at_k_answerable"))
            + " 可答精确:" + _fmt_rate(m.get("precision_at_k_answerable"))
            + " 可答MRR:" + _fmt_num(m.get("mrr_answerable"))
            + " 可答hit@1:" + _fmt_rate(m.get("hit_at_1_answerable"))
        ),
        (
            "grounded:" + _fmt_rate(m.get("grounded_rate"))
            + " useful:" + _fmt_rate(m.get("useful_rate"))
            + " 拒答率:" + _fmt_rate(m.get("refusal_rate"))
            + " 作答率:" + _fmt_rate(m.get("answer_rate"))
            + " 通过率:" + _fmt_rate(m.get("accepted_rate"))
            + " 平均置信度:" + _fmt_rate(m.get("mean_confidence"))
        ),
        (
            "改写率:" + _fmt_rate(m.get("rewrite_rate"))
            + " 纠错率:" + _fmt_rate(m.get("correction_rate"))
            + " 无关拒答率:" + _fmt_rate(m.get("probe_refusal_rate"))
            + " 隔离:" + ("通过" if m.get("scope_isolation_ok") else "未通过")
        ),
        (
            "检索延迟:" + _fmt_num(m.get("mean_latency_ms"))
            + "/" + _fmt_num(m.get("p95_latency_ms"))
            + "ms 整链路:" + _fmt_num(m.get("mean_total_latency_ms"))
            + "/" + _fmt_num(m.get("p95_total_latency_ms")) + "ms"
        ),
    ]
    for index, case in enumerate(cases[:12], start=1):
        question = (case.get("question") or "")[:28]
        result = "拒答" if case.get("refused") else ("回答" if case.get("grounded") is not None else "-")
        flags = []
        if case.get("rewrite_used"):
            flags.append("改写")
        if case.get("corrected"):
            flags.append("纠错")
        if case.get("is_probe"):
            flags.append("探针")
        lines.append(
            f"{index}.{question} | {result} g={case.get('grounded')} "
            f"u={case.get('useful')} c={_fmt_num(case.get('confidence'))} {' '.join(flags)}"
        )
    return "\n".join(lines)


def _limit(text: str) -> str:
    """把输出收敛到 200 字以内：优先在句子边界截断，硬上限 200。"""

    if len(text) <= MAX_ANALYSIS_CHARS:
        return text
    best = text[:MAX_ANALYSIS_CHARS]
    for separator in ("。", "；", "\n"):
        position = text.rfind(separator, 0, MAX_ANALYSIS_CHARS)
        if position > MAX_ANALYSIS_CHARS * 0.5:
            best = text[: position + 1]
            break
    return best


def analyze_results(metrics: dict, cases: list[dict]) -> str:
    """对一次已完成的评测运行输出 ≤200 字的分析。"""

    data = build_summary_text(metrics, cases)
    chain = ANALYSIS_PROMPT | get_llm() | StrOutputParser()
    raw = chain.invoke({"data": data, "limit": MAX_ANALYSIS_CHARS}).strip()
    return _limit(raw)
