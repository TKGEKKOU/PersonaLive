import re
from dataclasses import dataclass
from typing import Any


GROUP_EVENTS = frozenset({"GROUP_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE"})
C2C_EVENTS = frozenset({"C2C_MESSAGE_CREATE"})


@dataclass(frozen=True)
class QqOfficialMessage:
    event_name: str
    message_type: str  # "group" | "c2c"
    chat_id: str
    user_id: str
    msg_id: str
    text: str
    raw_content: str
    is_at: bool


def parse_message_event(
    event_name: str,
    data: dict[str, Any],
    bot_openid: str | None = None,
) -> QqOfficialMessage | None:
    """把 QQ 官方网关消息事件解析为内部消息结构。

    - GROUP_AT_MESSAGE_CREATE 仅在 @ 机器人时触发，视为 is_at；
    - GROUP_MESSAGE_CREATE 为全量模式，通过 mentions / content 判断是否 @ 机器人；
    - C2C_MESSAGE_CREATE 为单聊，无触发条件。
    """
    if event_name in GROUP_EVENTS:
        group_openid = str(data.get("group_openid") or "")
        if not group_openid:
            return None
        author = data.get("author") or {}
        user_id = str(author.get("member_openid") or author.get("id") or "")
        content = str(data.get("content") or "")
        text, is_at = _clean_group_content(content, event_name, data, bot_openid)
        return QqOfficialMessage(
            event_name=event_name,
            message_type="group",
            chat_id=group_openid,
            user_id=user_id,
            msg_id=str(data.get("id") or ""),
            text=text,
            raw_content=content,
            is_at=is_at,
        )
    if event_name in C2C_EVENTS:
        author = data.get("author") or {}
        user_openid = str(author.get("user_openid") or author.get("id") or "")
        if not user_openid:
            return None
        content = str(data.get("content") or "")
        return QqOfficialMessage(
            event_name=event_name,
            message_type="c2c",
            chat_id=user_openid,
            user_id=user_openid,
            msg_id=str(data.get("id") or ""),
            text=content,
            raw_content=content,
            is_at=True,
        )
    return None


def _clean_group_content(
    content: str,
    event_name: str,
    data: dict[str, Any],
    bot_openid: str | None,
) -> tuple[str, bool]:
    is_at = event_name == "GROUP_AT_MESSAGE_CREATE"
    if not is_at and bot_openid:
        mentions = data.get("mentions") or []
        is_at = any(
            isinstance(m, dict) and str(m.get("id") or "") == bot_openid
            for m in mentions
        )
    if not is_at and bot_openid and f"<@!{bot_openid}>" in content:
        is_at = True
    # 去掉 @ 片段（官方全量消息的 content 已去除机器人 @ 前缀，这里兜底清理）
    text = re.sub(r"<@![^>]+>", "", content).strip()
    return text, is_at
