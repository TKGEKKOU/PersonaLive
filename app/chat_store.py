"""Best-effort persistence of text conversation turns.

Typed and voice-mode turns are stored in conversation_messages so the chat
history survives reloads and stays consistent with the LangGraph checkpoint.
Persistence never blocks or fails a turn: use try_persist_text_message().
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.models import ConversationMessage


def persist_text_message(
    session_factory: Callable[[], Any],
    *,
    workspace_id: str,
    persona_id: str,
    conversation_id: str,
    role: str,
    content: str,
) -> None:
    session = session_factory()
    try:
        session.add(
            ConversationMessage(
                workspace_id=workspace_id,
                persona_id=persona_id,
                conversation_id=conversation_id,
                role=role,
                kind="text",
                content=content or "",
                status="completed",
            )
        )
        session.commit()
    finally:
        session.close()


def try_persist_text_message(
    session_factory: Callable[[], Any],
    **kwargs: Any,
) -> None:
    try:
        persist_text_message(session_factory, **kwargs)
    except Exception:
        pass
