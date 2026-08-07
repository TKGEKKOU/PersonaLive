from rag.eval.runner import run_eval
from rag.output_parsers import AnswerQualityScore, BatchDocumentScore
from rag.service import RagResult


class FakeRetriever:
    def invoke(self, query):
        return []


class FakeService:
    def query(self, request, on_step=None):
        return RagResult(
            answer_draft="资料中没有足够信息回答这个问题。",
            evidence=(),
            confidence=0.0,
            used_web_search=False,
            trace=(),
            grounded=False,
            useful=False,
            missing_points=("无资料",),
        )


def test_run_eval_offline_with_progress_and_answer_draft(monkeypatch):
    events = []
    monkeypatch.setattr("rag.eval.runner.build_retriever", lambda context, k: FakeRetriever())
    monkeypatch.setattr("rag.eval.runner.create_rag_service", lambda: FakeService())

    results = run_eval(
        [{"question": "q1", "expected_chunk_ids": [], "reference_answer": None}],
        persona_id="p1",
        workspace_id="local",
        knowledge_space_ids=["s1"],
        include_probes=False,
        progress=lambda done, total: events.append((done, total)),
    )

    assert len(results) == 1
    assert results[0].answer == "资料中没有足够信息回答这个问题。"
    assert events[-1] == (1, 1)


def test_run_eval_does_not_double_append_probes_when_dataset_has_them(monkeypatch):
    monkeypatch.setattr("rag.eval.runner.build_retriever", lambda context, k: FakeRetriever())
    monkeypatch.setattr("rag.eval.runner.create_rag_service", lambda: FakeService())

    results = run_eval(
        [
            {"question": "q1"},
            {"question": "无关问题A", "_probe": True},
            {"question": "无关问题B", "_probe": True},
        ],
        persona_id="p1",
        workspace_id="local",
        knowledge_space_ids=["s1"],
    )

    assert len(results) == 3
    assert [case.is_probe for case in results] == [False, True, True]


def test_run_eval_auto_judges_retrieval_relevance(monkeypatch):
    from langchain_core.documents import Document

    documents = [
        Document(page_content=f"内容{i}", metadata={"chunk_id": f"c{i}", "doc_id": "d"})
        for i in range(3)
    ]

    class PoolRetriever:
        def invoke(self, query):
            return documents

    monkeypatch.setattr("rag.eval.runner.build_retriever", lambda context, k: PoolRetriever())
    monkeypatch.setattr("rag.eval.runner.create_rag_service", lambda: FakeService())
    monkeypatch.setattr(
        "rag.eval.runner.grade_retrieved_documents",
        lambda question, pool, max_chars=1600, strict=True: BatchDocumentScore(
            relevant_ids=[0, 2], confidence=0.9
        ),
    )

    results = run_eval(
        [{"question": "q1"}],
        persona_id="p1",
        workspace_id="local",
        knowledge_space_ids=["s1"],
        include_probes=False,
    )

    assert results[0].expected_source == "auto"
    assert results[0].expected_ids == ["c0", "c2"]
    assert results[0].retrieved_ids == ["c0", "c1", "c2"]


def test_run_eval_refusal_is_not_regraded(monkeypatch):
    monkeypatch.setattr("rag.eval.runner.build_retriever", lambda context, k: FakeRetriever())
    monkeypatch.setattr("rag.eval.runner.create_rag_service", lambda: FakeService())

    def fail(*args, **kwargs):
        raise AssertionError("拒答不应再调用质量门评分")

    monkeypatch.setattr("rag.eval.runner.grade_answer_quality", fail)

    results = run_eval(
        [{"question": "q1"}],
        persona_id="p1",
        workspace_id="local",
        knowledge_space_ids=["s1"],
        include_probes=False,
    )

    assert results[0].refused is True
    assert results[0].grounded is None
    assert results[0].useful is None
    assert results[0].accepted is False


def test_run_eval_trace_marks_rewrite_and_correction(monkeypatch):
    class AdaptiveService:
        def query(self, request, on_step=None):
            return RagResult(
                answer_draft="有依据的答案",
                evidence=({"content": "片段"},),
                confidence=0.9,
                used_web_search=False,
                trace=(
                    {"node": "retrieve", "document_count": 4, "confidence": None, "has_answer": False},
                    {"node": "batch_grade_documents", "document_count": 0, "confidence": 0.0, "has_answer": False},
                    {"node": "rewrite_query", "document_count": 0, "confidence": 0.0, "has_answer": False},
                    {"node": "generate", "document_count": 1, "confidence": 0.9, "has_answer": True},
                    {"node": "quality_gate", "document_count": 1, "confidence": 0.9, "has_answer": True},
                    {"node": "prepare_correction", "document_count": 1, "confidence": 0.9, "has_answer": True},
                ),
                grounded=True,
                useful=True,
                missing_points=(),
            )

    monkeypatch.setattr("rag.eval.runner.build_retriever", lambda context, k: FakeRetriever())
    monkeypatch.setattr("rag.eval.runner.create_rag_service", lambda: AdaptiveService())

    results = run_eval(
        [{"question": "q1", "_complex": True}],
        persona_id="p1",
        workspace_id="local",
        knowledge_space_ids=["s1"],
        include_probes=False,
    )

    assert results[0].rewrite_used is True
    assert results[0].rewrite_count == 1
    assert results[0].corrected is True
    assert results[0].correction_count == 1
    assert results[0].is_complex is True


def test_run_eval_quality_regrade_uses_evidence_content(monkeypatch):
    captured = {}

    class AcceptedService:
        def query(self, request, on_step=None):
            return RagResult(
                answer_draft="有依据的答案",
                evidence=({"content": "真实片段内容"},),
                confidence=0.9,
                used_web_search=False,
                trace=(),
                grounded=True,
                useful=True,
                missing_points=(),
            )

    def fake_grade(question, documents_text, answer):
        captured["documents_text"] = documents_text
        return AnswerQualityScore(grounded=True, useful=True)

    monkeypatch.setattr("rag.eval.runner.build_retriever", lambda context, k: FakeRetriever())
    monkeypatch.setattr("rag.eval.runner.create_rag_service", lambda: AcceptedService())
    monkeypatch.setattr("rag.eval.runner.grade_answer_quality", fake_grade)

    results = run_eval(
        [{"question": "q1"}],
        persona_id="p1",
        workspace_id="local",
        knowledge_space_ids=["s1"],
        include_probes=False,
    )

    assert "真实片段内容" in captured["documents_text"]
    assert results[0].grounded is True
    assert results[0].useful is True
    assert results[0].accepted is True


def test_run_eval_step_callback_receives_question_and_label(monkeypatch):
    events = []

    class StepService:
        def query(self, request, on_step=None):
            if on_step:
                on_step("retrieve", {})
                on_step("quality_gate", {})
            return RagResult(
                answer_draft="有依据的答案",
                evidence=({"content": "片段"},),
                confidence=0.9,
                used_web_search=False,
                trace=(),
                grounded=True,
                useful=True,
                missing_points=(),
            )

    monkeypatch.setattr("rag.eval.runner.build_retriever", lambda context, k: FakeRetriever())
    monkeypatch.setattr("rag.eval.runner.create_rag_service", lambda: StepService())
    monkeypatch.setattr(
        "rag.eval.runner.grade_answer_quality",
        lambda question, documents_text, answer: AnswerQualityScore(grounded=True, useful=True),
    )

    run_eval(
        [{"question": "今天天气如何"}],
        persona_id="p1",
        workspace_id="local",
        knowledge_space_ids=["s1"],
        include_probes=False,
        step_callback=lambda index, question, step: events.append((index, question, step)),
    )

    assert events == [
        (1, "今天天气如何", "检索知识库"),
        (1, "今天天气如何", "质量检查"),
    ]
