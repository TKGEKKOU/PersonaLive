from collections.abc import Callable
from dataclasses import dataclass

from settings import Settings
from rag.contracts import RagQueryContext


@dataclass(frozen=True)
class RagRequest:
    """RAG 统一输入；context 必须由服务端从当前角色派生。"""

    question: str
    context: RagQueryContext
    allow_web_fallback: bool = False
    persona_name: str = ""
    persona_profile: dict | None = None
    available_tools: tuple[str, ...] = ()
    force_knowledge: bool = False


@dataclass(frozen=True)
class RagResult:
    """供 Agent/API 消费的稳定输出，不暴露 LangGraph 内部状态。"""

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

    def query(self, request: RagRequest, on_step: Callable | None = None) -> RagResult:
        if not request.question.strip():
            raise ValueError("question must not be empty")
        return self._runner(request, on_step=on_step)


# 管线工厂：按配置选择 simple（纯检索生成）或 adaptive（纠正式 RAG）。
# 延迟导入避免 simple 模式下初始化 Adaptive 图及其模型依赖；调用方只需
# RagService.query(RagRequest)，无需关心内部图实现。
def create_rag_service(settings: Settings | None = None) -> RagService:
    """在应用启动时选择 Pipeline，调用方无需了解具体图实现。"""

    active_settings = settings or Settings.load()
    # 延迟导入避免 simple 模式初始化 Adaptive 图及其模型依赖。
    if active_settings.rag_pipeline == "simple":
        from rag.simple_graph import run_simple

        return RagService(run_simple)
    if active_settings.rag_pipeline in {"default", "adaptive"}:
        from rag.adaptive_graph import run_adaptive

        return RagService(run_adaptive)
    raise ValueError(f"Unsupported RAG_PIPELINE: {active_settings.rag_pipeline}")
