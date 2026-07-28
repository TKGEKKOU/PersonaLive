from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


class TextSubmitEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text.submit"]
    question: str = Field(min_length=1, max_length=2000)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("question must not be empty")
        return value


class CancelEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["generation.cancel"]


class ConfirmationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["confirmation.respond"]
    approved: bool
    specialist: Literal["conversation", "web", "memory", "management"]


class PingEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["session.ping"]


ClientEvent = Annotated[
    TextSubmitEvent | CancelEvent | ConfirmationEvent | PingEvent,
    Field(discriminator="type"),
]
_CLIENT_EVENT_ADAPTER = TypeAdapter(ClientEvent)


def parse_client_event(payload: object) -> ClientEvent:
    return _CLIENT_EVENT_ADAPTER.validate_python(payload)


def server_event(
    event_type: str,
    *,
    turn_id: str | None = None,
    **payload: Any,
) -> dict[str, Any]:
    event: dict[str, Any] = {"type": event_type}
    if turn_id is not None:
        event["turn_id"] = turn_id
    event.update(payload)
    return event
