from dataclasses import asdict, dataclass
from typing import Any, Literal

from rag.contracts import RagEvidenceResult


SpecialistStatus = Literal[
    "accepted",
    "insufficient",
    "confirmation_required",
    "completed",
    "failed",
]


@dataclass(frozen=True)
class SpecialistResult:
    """Worker 与 Supervisor 之间的稳定合同，避免依赖自然语言模板解析。"""

    specialist: Literal["knowledge", "web", "memory", "management"]
    status: SpecialistStatus
    answer: str = ""
    evidence: tuple[dict[str, Any], ...] = ()
    citations: tuple[dict[str, Any], ...] = ()
    uncertainties: tuple[str, ...] = ()
    trace: tuple[dict[str, Any], ...] = ()
    confidence: float = 0.0
    pending_action: dict[str, Any] | None = None

    @classmethod
    def from_rag_evidence(cls, result: RagEvidenceResult) -> "SpecialistResult":
        return cls(
            specialist="knowledge",
            status=result.status,
            answer=result.answer,
            evidence=result.evidence,
            citations=result.citations,
            uncertainties=result.uncertainties,
            trace=result.trace,
            confidence=result.confidence,
        )

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        # ToolMessage 经过 JSON 序列化后本来就是数组；这里直接返回 list，方便 API 与测试消费。
        for key in ("evidence", "citations", "uncertainties", "trace"):
            value[key] = list(value[key])
        return value
