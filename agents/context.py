from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class PersonaAgentContext:
    """Agent 的不可变上下文（对应 LangGraph 的 context_schema）。

    作用域字段（workspace_id / knowledge_space_ids / persona_id）由服务端从路径解析，
    绝不信任模型输入——所有工具据此做数据边界过滤；session_factory 让工具按需打开
    数据库会话，避免跨请求共享连接。frozen 保证运行期不被工具意外改写。
    """

    persona_id: str
    workspace_id: str
    knowledge_space_ids: tuple[str, ...]
    conversation_id: str
    persona_name: str
    persona_type: str
    persona_profile: dict[str, Any] = field(default_factory=dict)
    session_factory: Callable[[], Session] | None = None

    def __post_init__(self) -> None:
        # 没有知识空间意味着该角色不可检索，属于配置错误而非合法状态。
        if not self.knowledge_space_ids:
            raise ValueError("knowledge_space_ids must not be empty")
