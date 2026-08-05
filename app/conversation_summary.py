"""会话级摘要：每 10 轮后台增量压缩对话历史。

纯 best-effort：任何失败只记日志，绝不阻塞或失败对话轮。摘要供
Supervisor 上下文注入（见 agents/workflow.py）。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select

from app.models import ConversationMessage, ConversationSummary
from rag.llm import get_llm


logger = logging.getLogger(__name__)

CONVERSATION_SUMMARY_INTERVAL = 10

_summary_locks: dict[tuple[str, str], threading.Lock] = {}
_summary_locks_guard = threading.Lock()


def _lock_for(persona_id: str, conversation_id: str) -> threading.Lock:
    key = (persona_id, conversation_id)
    with _summary_locks_guard:
        lock = _summary_locks.get(key)
        if lock is None:
            lock = _summary_locks[key] = threading.Lock()
        return lock


def count_user_turns(
    session: Any,
    workspace_id: str,
    persona_id: str,
    conversation_id: str,
) -> int:
    """统计已完成且非空的用户消息数（文本与语音转写均计入）。"""

    rows = session.scalars(
        select(ConversationMessage).where(
            ConversationMessage.workspace_id == workspace_id,
            ConversationMessage.persona_id == persona_id,
            ConversationMessage.conversation_id == conversation_id,
            ConversationMessage.role == "user",
            ConversationMessage.kind.in_(("text", "audio")),
            ConversationMessage.status == "completed",
            ConversationMessage.content != "",
        )
    )
    return len(list(rows))


def load_recent_turns(
    session: Any,
    workspace_id: str,
    persona_id: str,
    conversation_id: str,
    after_count: int,
    up_to_count: int,
) -> str:
    """取第 after_count+1 到第 up_to_count 条用户消息及其回复的文本。"""

    rows = list(
        session.scalars(
            select(ConversationMessage)
            .where(
                ConversationMessage.workspace_id == workspace_id,
                ConversationMessage.persona_id == persona_id,
                ConversationMessage.conversation_id == conversation_id,
                ConversationMessage.status == "completed",
                ConversationMessage.content != "",
            )
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
        )
    )
    user_rows = [m for m in rows if m.role == "user" and m.kind in ("text", "audio")]
    if not user_rows:
        return ""
    selected = user_rows[after_count:up_to_count]
    if not selected:
        return ""
    selected_ids = {m.id for m in selected}
    lines: list[str] = []
    for message in rows:
        if message.id in selected_ids:
            lines.append(f"{message.role}: {message.content}")
            continue
        if lines and message.role == "assistant" and message.kind == "text":
            lines.append(f"assistant: {message.content}")
    return "\n".join(lines)


def build_summary_prompt(previous_summary: str, turns_text: str) -> str:
    """增量摘要 prompt：合并已有摘要与新增对话。"""

    previous_block = previous_summary.strip() or "（无）"
    return (
        "你是会话摘要器。把已有摘要与新增对话合并，输出更新后的会话摘要。\n"
        "只保留：关键事实、用户偏好、未完成事项、重要约定。\n"
        "去除寒暄与重复，输出上限约 500 字（中文）。\n\n"
        f"已有摘要：\n{previous_block}\n\n"
        f"新增对话：\n{turns_text}\n\n"
        "更新后的摘要："
    )


def _generate_summary(prompt: str) -> str:
    response = get_llm().invoke(
        [
            SystemMessage(content="你是会话摘要器。"),
            HumanMessage(content=prompt),
        ]
    )
    return str(response.content).strip()


def get_conversation_summary(
    session: Any,
    workspace_id: str,
    persona_id: str,
    conversation_id: str,
) -> str:
    row = session.scalars(
        select(ConversationSummary).where(
            ConversationSummary.workspace_id == workspace_id,
            ConversationSummary.persona_id == persona_id,
            ConversationSummary.conversation_id == conversation_id,
        )
    ).first()
    return row.summary if row is not None else ""


def _maybe_summarize(
    session_factory: Callable[[], Any],
    *,
    workspace_id: str,
    persona_id: str,
    conversation_id: str,
) -> None:
    lock = _lock_for(persona_id, conversation_id)
    with lock:
        try:
            session = session_factory()
            try:
                count = count_user_turns(
                    session, workspace_id, persona_id, conversation_id
                )
                if count % CONVERSATION_SUMMARY_INTERVAL != 0:
                    return
                row = session.scalars(
                    select(ConversationSummary).where(
                        ConversationSummary.workspace_id == workspace_id,
                        ConversationSummary.persona_id == persona_id,
                        ConversationSummary.conversation_id == conversation_id,
                    )
                ).first()
                if row is not None and row.summarized_through_count >= count:
                    return
                previous = row.summary if row is not None else ""
                turns = load_recent_turns(
                    session,
                    workspace_id,
                    persona_id,
                    conversation_id,
                    row.summarized_through_count if row is not None else 0,
                    count,
                )
                summary = _generate_summary(build_summary_prompt(previous, turns))
                if not summary:
                    return
                if row is None:
                    session.add(
                        ConversationSummary(
                            workspace_id=workspace_id,
                            persona_id=persona_id,
                            conversation_id=conversation_id,
                            summary=summary,
                            summarized_through_count=count,
                        )
                    )
                else:
                    row.summary = summary
                    row.summarized_through_count = count
                session.commit()
            finally:
                session.close()
        except Exception:
            logger.exception(
                "会话摘要失败 persona=%s conversation=%s", persona_id, conversation_id
            )


def schedule_summary_after_turn(
    session_factory: Callable[[], Any],
    *,
    workspace_id: str,
    persona_id: str,
    conversation_id: str,
) -> None:
    """保存一轮对话后调度后台摘要；不阻塞、失败静默。"""

    try:
        thread = threading.Thread(
            target=_maybe_summarize,
            args=(session_factory,),
            kwargs={
                "workspace_id": workspace_id,
                "persona_id": persona_id,
                "conversation_id": conversation_id,
            },
            name=f"summary-{persona_id[:8]}",
            daemon=True,
        )
        thread.start()
    except Exception:
        logger.exception("无法启动会话摘要线程")
