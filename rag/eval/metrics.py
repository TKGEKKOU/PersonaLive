"""RAG 离线评测指标（纯计算，不依赖 Milvus/LLM，便于单测与回归门禁）。

检索类指标基于"检索器输出"评估，与生成无关：
  recall@k   —— 相关片段被召回的比例（漏召回越小越好）
  precision@k —— 召回结果中相关片段占比（噪声越小越好）
  MRR        —— 第一个相关片段排名的倒数（排序质量）
  hit@1      —— 首个位置是否命中
延迟指标：mean / p95，用于监控检索与整链路的性能回归。
"""

from __future__ import annotations

from statistics import mean


def recall_at_k(retrieved: list[str], expected: set[str], k: int | None = None) -> float:
    """Recall@k：命中的相关片段数 / 相关片段总数。"""

    if not expected:
        return 0.0
    top = retrieved if k is None else retrieved[:k]
    hits = sum(1 for doc_id in top if doc_id in expected)
    return hits / len(expected)


def precision_at_k(retrieved: list[str], expected: set[str], k: int | None = None) -> float:
    """Precision@k：召回结果中相关片段占比。"""

    if not retrieved:
        return 0.0
    top = retrieved if k is None else retrieved[:k]
    return sum(1 for doc_id in top if doc_id in expected) / len(top)


def mrr(retrieved: list[str], expected: set[str]) -> float:
    """MRR：第一个相关片段的排名倒数；无命中返回 0。"""

    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in expected:
            return 1.0 / rank
    return 0.0


def hit_at_k(retrieved: list[str], expected: set[str], k: int = 1) -> float:
    """Hit@k：前 k 位是否至少命中一个相关片段。"""

    return 1.0 if any(doc_id in expected for doc_id in retrieved[:k]) else 0.0


def average(values: list[float]) -> float:
    return mean(values) if values else 0.0


def p95(values: list[float]) -> float:
    """95 分位延迟；空列表返回 0。"""

    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


def summarize_retrieval(cases: list[dict]) -> dict:
    """汇总检索与延迟指标。

    Args:
        cases: 每个元素至少含 retrieved_ids / expected_ids，可选 latency_ms。
              缺少 expected_ids 的用例不计入检索指标（但计入延迟）。
    """

    recall: list[float] = []
    precision: list[float] = []
    mrr_values: list[float] = []
    hit1: list[float] = []
    for case in cases:
        if not case.get("expected_ids"):
            continue
        retrieved = case.get("retrieved_ids", [])
        expected = set(case["expected_ids"])
        recall.append(recall_at_k(retrieved, expected))
        precision.append(precision_at_k(retrieved, expected))
        mrr_values.append(mrr(retrieved, expected))
        hit1.append(hit_at_k(retrieved, expected))
    latencies = [
        case["retrieval_latency_ms"]
        for case in cases
        if case.get("retrieval_latency_ms") is not None
    ]
    return {
        "recall_at_k": average(recall),
        "precision_at_k": average(precision),
        "mrr": average(mrr_values),
        "hit_at_1": average(hit1),
        "mean_latency_ms": average(latencies),
        "p95_latency_ms": p95(latencies),
        "cases_with_expected": len(recall),
    }


def summarize_generation(cases: list[dict]) -> dict:
    """汇总生成质量指标（复用质量门的 grounded/useful 判定）。"""

    grounded = [case["grounded"] for case in cases if case.get("grounded") is not None]
    useful = [case["useful"] for case in cases if case.get("useful") is not None]
    return {
        "grounded_rate": average([1.0 if value else 0.0 for value in grounded]) if grounded else None,
        "useful_rate": average([1.0 if value else 0.0 for value in useful]) if useful else None,
        "cases_checked": len(grounded),
    }
