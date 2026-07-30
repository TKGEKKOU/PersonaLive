from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PersonaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    profile: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be empty")
        return value


class PersonaResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    workspace_id: str
    knowledge_space_id: str
    persona_type: Literal["character", "knowledge_expert"]
    profile: dict[str, Any] = Field(validation_alias="profile_json")
    status: str


class PersonaDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    profile: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def strip_draft_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name must not be empty")
        return value


class PersonaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    profile: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("name must not be empty")
        return value


class PersonaDraftResponse(BaseModel):
    id: str
    mode: Literal["character", "expert"]
    persona_type: Literal["character", "knowledge_expert"]
    candidates: list[dict[str, Any]]
    selected_candidate_id: str | None
    suggested_name: str
    profile: dict[str, Any]
    status: str
    documents: list["DocumentJobResponse"]
    persona: PersonaResponse | None = None


class DocumentJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workspace_id: str
    knowledge_space_id: str
    document_id: str
    original_filename: str
    markdown_filename: str
    markdown_preview: str | None
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None


class RagQueryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = Field(default=None, max_length=255)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be empty")
        return value


class RagQueryResponse(BaseModel):
    answer: str
    evidence: list[dict[str, Any]]
    confidence: float
    used_web_search: bool
    trace: list[dict[str, Any]]
    grounded: bool
    useful: bool
    missing_points: list[str]
    interaction_mode: Literal["conversation", "capability", "knowledge", "web"] = "knowledge"


class AgentQueryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    conversation_id: str = Field(min_length=1, max_length=255)

    @field_validator("question", "conversation_id")
    @classmethod
    def strip_agent_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be empty")
        return value


class AgentResumePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=255)
    specialist: Literal["conversation", "web", "memory", "management"]
    approved: bool


class AgentTurnResponse(BaseModel):
    status: Literal["completed", "pending_confirmation"]
    answer: str
    specialist: Literal["conversation", "web", "memory", "management"]
    pending_action: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[dict[str, Any]] = Field(default_factory=list)


class LocalSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    embedding_api_key: str | None = None
    embedding_base_url: str | None = None
    embedding_model: str | None = None
    embedding_dimensions: int | None = Field(default=None, ge=1, le=4096)
    embedding_send_dimensions: bool | None = None
    web_search_provider: str | None = None
    web_search_api_key: str | None = None
    web_search_base_url: str | None = None
    tavily_api_key: str | None = None
    enable_web_fallback: bool | None = None


class LocalSettingsResponse(BaseModel):
    openai_api_key_configured: bool
    openai_base_url: str
    openai_model: str
    embedding_api_key_configured: bool
    embedding_base_url: str
    embedding_model: str
    embedding_dimensions: int
    embedding_send_dimensions: bool
    web_search_provider: str
    web_search_api_key_configured: bool
    web_search_base_url: str
    enable_web_fallback: bool
    restart_required: bool = False


class TranscriptionResponse(BaseModel):
    text: str


class ConversationMessageResponse(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    kind: Literal["text", "audio"]
    content: str
    audio_url: str | None = None
    transcript: str | None = None
    status: Literal["pending", "transcribing", "completed", "failed"]
    error_message: str | None = None
    created_at: datetime


class VoiceMessageTurnResponse(BaseModel):
    message: ConversationMessageResponse
    turn: AgentTurnResponse
