from dataclasses import dataclass


@dataclass(frozen=True)
class RagQueryContext:
    persona_id: str
    workspace_id: str
    knowledge_space_ids: tuple[str, ...]
    conversation_id: str | None = None

    def __post_init__(self) -> None:
        if not self.knowledge_space_ids:
            raise ValueError("knowledge_space_ids must not be empty")
