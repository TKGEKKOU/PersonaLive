from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class PersonaAgentContext:
    persona_id: str
    workspace_id: str
    knowledge_space_ids: tuple[str, ...]
    conversation_id: str
    persona_name: str
    persona_type: str
    persona_profile: dict[str, Any] = field(default_factory=dict)
    session_factory: Callable[[], Session] | None = None

    def __post_init__(self) -> None:
        if not self.knowledge_space_ids:
            raise ValueError("knowledge_space_ids must not be empty")

