import pytest

from rag.eval.metrics import (
    hit_at_k,
    mrr,
    p95,
    precision_at_k,
    recall_at_k,
    summarize_generation,
    summarize_retrieval,
)


def test_recall_precision_mrr_and_hit():
    retrieved = ["a", "b", "c", "d"]
    expected = {"b", "d", "e"}
    assert recall_at_k(retrieved, expected, k=4) == 2 / 3
    assert precision_at_k(retrieved, expected, k=4) == 0.5
    assert mrr(retrieved, expected) == 0.5  # first hit at rank 2
    assert hit_at_k(retrieved, expected, k=1) == 0.0
    assert hit_at_k(retrieved, expected, k=2) == 1.0


def test_metrics_ignore_cases_without_expected_ids():
    summary = summarize_retrieval(
        [
            {"retrieved_ids": ["a"], "expected_ids": ["a"], "retrieval_latency_ms": 10},
            {"retrieved_ids": ["x"], "expected_ids": [], "retrieval_latency_ms": 20},
        ]
    )
    assert summary["recall_at_k"] == 1.0
    assert summary["cases_with_expected"] == 1
    assert summary["mean_latency_ms"] == 15.0


def test_summarize_retrieval_counts_auto_judged_empty_expected():
    summary = summarize_retrieval(
        [
            {
                "retrieved_ids": ["a", "b"],
                "expected_ids": [],
                "expected_source": "auto",
                "retrieval_latency_ms": 10,
            },
        ]
    )
    assert summary["recall_at_k"] == 0.0
    assert summary["precision_at_k"] == 0.0
    assert summary["cases_with_expected"] == 1
    assert summary["cases_total"] == 1


def test_summarize_retrieval_reports_answerable_split():
    summary = summarize_retrieval(
        [
            {
                "retrieved_ids": ["a", "b", "c", "d"],
                "expected_ids": ["a", "d"],
                "expected_source": "labeled",
                "retrieval_latency_ms": 10,
            },
            {
                "retrieved_ids": ["x", "y"],
                "expected_ids": [],
                "expected_source": "auto",
                "retrieval_latency_ms": 20,
            },
        ]
    )
    assert summary["cases_answerable"] == 1
    assert summary["recall_at_k_answerable"] == 1.0
    assert summary["precision_at_k_answerable"] == 0.5
    assert summary["mrr_answerable"] == 1.0
    assert summary["hit_at_1_answerable"] == 1.0


def test_summarize_generation_splits_refusal_from_quality():
    summary = summarize_generation(
        [
            {
                "grounded": True,
                "useful": True,
                "refused": False,
                "accepted": True,
                "confidence": 0.9,
                "total_latency_ms": 100,
                "is_probe": False,
            },
            {
                "grounded": None,
                "useful": None,
                "refused": True,
                "accepted": False,
                "confidence": 0.0,
                "total_latency_ms": 50,
                "rewrite_used": True,
                "corrected": False,
                "is_probe": True,
            },
            {
                "grounded": True,
                "useful": True,
                "refused": False,
                "accepted": True,
                "confidence": 0.8,
                "total_latency_ms": 80,
                "rewrite_used": False,
                "corrected": True,
                "is_probe": False,
            },
        ]
    )
    assert summary["grounded_rate"] == 1.0
    assert summary["useful_rate"] == 1.0
    assert summary["cases_checked"] == 2
    assert summary["cases_total"] == 3
    assert summary["cases_refused"] == 1
    assert summary["refusal_rate"] == 1 / 3
    assert summary["answer_rate"] == 2 / 3
    assert summary["accepted_rate"] == 2 / 3
    assert summary["mean_confidence"] == pytest.approx((0.9 + 0.0 + 0.8) / 3)
    assert summary["rewrite_rate"] == 1 / 3
    assert summary["correction_rate"] == 1 / 3
    assert summary["probe_refusal_rate"] == 1.0
    assert summary["mean_total_latency_ms"] == (100 + 50 + 80) / 3


def test_summarize_generation_tracks_complex_and_counts():
    summary = summarize_generation(
        [
            {
                "grounded": True,
                "useful": True,
                "refused": False,
                "accepted": True,
                "confidence": 0.9,
                "total_latency_ms": 100,
                "rewrite_used": False,
                "rewrite_count": 0,
                "corrected": True,
                "correction_count": 1,
                "is_complex": True,
                "is_probe": False,
            },
            {
                "grounded": None,
                "useful": None,
                "refused": True,
                "accepted": False,
                "confidence": 0.0,
                "total_latency_ms": 50,
                "rewrite_used": True,
                "rewrite_count": 2,
                "corrected": False,
                "correction_count": 0,
                "is_complex": False,
                "is_probe": True,
            },
        ]
    )
    assert summary["mean_rewrite_count"] == 1.0
    assert summary["mean_correction_count"] == 0.5
    assert summary["cases_complex"] == 1
    assert summary["complex_rewrite_rate"] == 0.0
    assert summary["complex_correction_rate"] == 1.0


def test_p95_and_empty_values():
    assert p95([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]) == 20
    assert p95([]) == 0.0
