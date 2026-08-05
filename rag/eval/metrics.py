"""RAG 离线评测指标（纯计算，不依赖 Milvus/LLM，便于单测与回归门禁）。

检索类指标基于"检索器输出"评估，与生成无关：
  recall@k   —— 相关片段被召回的比例（漏召回越小越好）
  precision@k —— 召回结果中相关片段占比（噪声越小越好）
  MRR        —— 第一个相关片段排名的倒数（排序质量）
  hit@1      —— 首个位置是否命中
生成类指标：grounded/useful 只统计"非拒答"的答案；拒答、通过质量门、置信度
单独统计，避免把正确拒答误判为生成质量差。
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
        cases: 每个元素至少含 retrieved_ids / expected_ids / expected_source，
              可选 latency_ms。人工标注模式下缺少 expected_ids 的用例不计入
              检索指标；免标注模式（expected_source == "auto"）即使判定出
              0 条相关片段也计入（视为零召回），避免掩盖检索失败。
    """

    recall: list[float] = []
    precision: list[float] = []
    mrr_values: list[float] = []
    hit1: list[float] = []
    answerable_recall: list[float] = []
    answerable_precision: list[float] = []
    answerable_mrr: list[float] = []
    answerable_hit1: list[float] = []
    for case in cases:
        if not case.get("expected_ids") and case.get("expected_source") != "auto":
            continue
        retrieved = case.get("retrieved_ids", [])
        expected = set(case["expected_ids"])
        recall.append(recall_at_k(retrieved, expected))
        precision.append(precision_at_k(retrieved, expected))
        mrr_values.append(mrr(retrieved, expected))
        hit1.append(hit_at_k(retrieved, expected))
        # 有相关片段可依的用例单独统计，避免无关/拒答问题把检索指标稀释成 0。
        if case.get("expected_ids"):
            answerable_recall.append(recall_at_k(retrieved, expected))
            answerable_precision.append(precision_at_k(retrieved, expected))
            answerable_mrr.append(mrr(retrieved, expected))
            answerable_hit1.append(hit_at_k(retrieved, expected))
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
        "cases_judged": len(recall),
        "cases_total": len(cases),
        "cases_answerable": len(answerable_recall),
        "recall_at_k_answerable": average(answerable_recall),
        "precision_at_k_answerable": average(answerable_precision),
        "mrr_answerable": average(answerable_mrr),
        "hit_at_1_answerable": average(answerable_hit1),
    }


def summarize_generation(cases: list[dict]) -> dict:
    """汇总生成质量指标。

    grounded/useful 只统计非拒答且已评分的答案；拒答率、通过率、置信度、
    整链路延迟单列，给出更完整的生成侧画像。
    """

    graded = [case for case in cases if case.get("grounded") is not None]
    grounded = [case["grounded"] for case in graded]
    useful = [case["useful"] for case in graded]
    refused = [case for case in cases if case.get("refused")]
    accepted = [case for case in cases if case.get("accepted")]
    rewrote = [case for case in cases if case.get("rewrite_used")]
    corrected = [case for case in cases if case.get("corrected")]
    complex_cases = [case for case in cases if case.get("is_complex")]
    complex_rewrote = [case for case in complex_cases if case.get("rewrite_used")]
    complex_corrected = [case for case in complex_cases if case.get("corrected")]
    probes = [case for case in cases if case.get("is_probe")]
    probe_refused = [case for case in probes if case.get("refused")]
    confidences = [case["confidence"] for case in cases if case.get("confidence") is not None]
    latencies = [
        case["total_latency_ms"]
        for case in cases
        if case.get("total_latency_ms") is not None
    ]
    rewrite_counts = [case.get("rewrite_count", 0) for case in cases]
    correction_counts = [case.get("correction_count", 0) for case in cases]
    total = len(cases)
    return {
        "grounded_rate": average([1.0 if value else 0.0 for value in grounded]) if grounded else None,
        "useful_rate": average([1.0 if value else 0.0 for value in useful]) if useful else None,
        "cases_checked": len(graded),
        "cases_total": total,
        "cases_refused": len(refused),
        "cases_accepted": len(accepted),
        "refusal_rate": len(refused) / total if total else 0.0,
        "answer_rate": (total - len(refused)) / total if total else 0.0,
        "accepted_rate": len(accepted) / total if total else 0.0,
        "mean_confidence": average(confidences) if confidences else None,
        "rewrite_rate": len(rewrote) / total if total else 0.0,
        "correction_rate": len(corrected) / total if total else 0.0,
        "mean_rewrite_count": average(rewrite_counts),
        "mean_correction_count": average(correction_counts),
        "cases_complex": len(complex_cases),
        "complex_rewrite_rate": (
            len(complex_rewrote) / len(complex_cases) if complex_cases else None
        ),
        "complex_correction_rate": (
            len(complex_corrected) / len(complex_cases) if complex_cases else None
        ),
        "probe_refusal_rate": (
            len(probe_refused) / len(probes) if probes else None
        ),
        "mean_total_latency_ms": average(latencies),
        "p95_total_latency_ms": p95(latencies),
    }
