import pytest

from rag.contracts import RagEvidenceResult, RagQueryContext
from rag.service import RagRequest, RagResult, RagService


def test_rag_service_validates_question_and_returns_typed_result():
    context = RagQueryContext("persona-a", "local-default", ("space-a",))
    expected = RagResult.empty("No indexed evidence")
    service = RagService(lambda request, on_step=None: expected)

    assert service.query(RagRequest("facts", context)) == expected
    with pytest.raises(ValueError, match="question must not be empty"):
        service.query(RagRequest("   ", context))


def test_empty_result_is_fail_closed():
    result = RagResult.empty("insufficient evidence")

    assert result.confidence == 0.0
    assert result.grounded is False
    assert result.useful is False
    assert result.missing_points == ("insufficient evidence",)


def test_evidence_result_only_accepts_grounded_and_useful_answers():
    accepted = RagEvidenceResult.from_rag_result(
        RagResult(
            answer_draft="supported answer",
            evidence=({"content": "source"},),
            confidence=0.9,
            used_web_search=False,
            trace=({"node": "quality_gate"},),
            grounded=True,
            useful=True,
            missing_points=(),
        )
    )
    rejected = RagEvidenceResult.from_rag_result(
        RagResult(
            answer_draft="unsupported draft",
            evidence=({"content": "weak source"},),
            confidence=0.3,
            used_web_search=False,
            trace=({"node": "quality_gate"},),
            grounded=False,
            useful=True,
            missing_points=("missing cause",),
        )
    )

    assert accepted.status == "accepted"
    assert accepted.answer == "supported answer"
    assert rejected.status == "insufficient"
    assert rejected.answer == ""
    assert rejected.evidence == ()
    assert rejected.missing_points == ("missing cause",)
