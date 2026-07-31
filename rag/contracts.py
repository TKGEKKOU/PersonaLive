from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class RagQueryContext:
    persona_id: str
    workspace_id: str
    knowledge_space_ids: tuple[str, ...]
    conversation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.knowledge_space_ids:
            raise ValueError("knowledge_space_ids must not be empty")


@dataclass(frozen=True)
class RagEvidenceResult:
    """RAG 交给 Agent 的证据合同，不暴露未通过质量门禁的草稿。"""

    status: Literal["accepted", "insufficient"]
    answer: str
    evidence: tuple[dict[str, Any], ...]
    confidence: float
    citations: tuple[dict[str, Any], ...]
    uncertainties: tuple[str, ...]
    trace: tuple[dict[str, Any], ...]
    used_web_search: bool
    grounded: bool
    useful: bool
    missing_points: tuple[str, ...]

    @classmethod
    def from_rag_result(cls, result: Any) -> "RagEvidenceResult":
        accepted = bool(result.grounded and result.useful and result.evidence)
        # 门禁失败时主动丢弃答案和证据，防止上层模型把低质量草稿重新包装成事实。
        evidence = tuple(result.evidence) if accepted else ()
        missing_points = tuple(result.missing_points)
        uncertainties = missing_points if not accepted else ()
        return cls(
            status="accepted" if accepted else "insufficient",
            answer=result.answer_draft if accepted else "",
            evidence=evidence,
            confidence=float(result.confidence),
            citations=_citations_from_evidence(evidence),
            uncertainties=uncertainties,
            trace=tuple(result.trace),
            used_web_search=bool(result.used_web_search),
            grounded=bool(result.grounded),
            useful=bool(result.useful),
            missing_points=missing_points,
        )


def _citations_from_evidence(
    evidence: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    """提取可公开引用字段，原始片段仍保留在 evidence 中供 API 展示。"""

    citation_fields = ("source", "filename", "title", "section", "document_id", "chunk_id")
    return tuple(
        {key: item[key] for key in citation_fields if item.get(key) is not None}
        for item in evidence
    )
