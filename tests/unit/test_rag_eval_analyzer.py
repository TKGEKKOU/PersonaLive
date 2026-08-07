from rag.eval.analyzer import MAX_ANALYSIS_CHARS, _limit, build_summary_text


def test_limit_truncates_at_sentence_boundary():
    text = "好。" * 150
    limited = _limit(text)
    assert len(limited) <= MAX_ANALYSIS_CHARS + 1
    assert limited.endswith("。")


def test_limit_keeps_short_text():
    assert _limit("简短结论。") == "简短结论。"


def test_build_summary_text_includes_rates_and_cases():
    text = build_summary_text(
        {
            "cases_total": 5,
            "cases_answerable": 4,
            "recall_at_k_answerable": 0.8,
            "refusal_rate": 0.2,
            "scope_isolation_ok": True,
            "mean_latency_ms": 90.0,
        },
        [{"question": "问题A", "refused": False, "grounded": True, "useful": True, "confidence": 0.9}],
    )
    assert "80%" in text
    assert "20%" in text
    assert "通过" in text
    assert "问题A" in text
