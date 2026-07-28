from collections.abc import Callable
from dataclasses import dataclass

from settings import Settings
from rag.contracts import RagQueryContext


@dataclass(frozen=True)
class RagRequest:
    question: str
    context: RagQueryContext
    allow_web_fallback: bool = False
    persona_name: str = ""
    persona_profile: dict | None = None
    available_tools: tuple[str, ...] = ()
    force_knowledge: bool = False


@dataclass(frozen=True)
class RagResult:
    answer_draft: str
    evidence: tuple[dict, ...]
    confidence: float
    used_web_search: bool
    trace: tuple[dict, ...]
    grounded: bool
    useful: bool
    missing_points: tuple[str, ...]
    interaction_mode: str = "knowledge"

    @classmethod
    def empty(cls, reason: str) -> "RagResult":
        return cls(
            answer_draft=reason,
            evidence=(),
            confidence=0.0,
            used_web_search=False,
            trace=(),
            grounded=False,
            useful=False,
            missing_points=(reason,),
        )


class RagService:
    def __init__(self, runner: Callable[[RagRequest], RagResult]):
        self._runner = runner

    def query(self, request: RagRequest) -> RagResult:
        if not request.question.strip():
            raise ValueError("question must not be empty")
        return self._runner(request)


def create_rag_service(settings: Settings | None = None) -> RagService:
    active_settings = settings or Settings.load()
    if active_settings.rag_pipeline == "simple":
        from rag.simple_graph import run_simple

        return RagService(run_simple)
    if active_settings.rag_pipeline in {"default", "adaptive"}:
        from rag.adaptive_graph import run_adaptive

        return RagService(run_adaptive)
    raise ValueError(f"Unsupported RAG_PIPELINE: {active_settings.rag_pipeline}")
