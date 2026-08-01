import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OneBotMessage:
    post_type: str
    message_type: str
    user_id: str
    self_id: str
    text: str
    is_at: bool
    group_id: str | None = None


def parse_message_event(payload: dict) -> OneBotMessage | None:
    if not isinstance(payload, dict) or payload.get("post_type") != "message":
        return None
    message_type = str(payload.get("message_type") or "")
    if message_type not in {"private", "group"}:
        return None
    self_id = str(payload.get("self_id") or "")
    user_id = str(payload.get("user_id") or "")
    group_id = str(payload.get("group_id") or "") or None
    raw_message = str(payload.get("raw_message") or "")
    text, is_at = _extract_message(payload.get("message"), self_id, raw_message)
    return OneBotMessage(
        post_type="message",
        message_type=message_type,
        user_id=user_id,
        self_id=self_id,
        text=text,
        is_at=is_at,
        group_id=group_id,
    )


def _extract_message(message: Any, self_id: str, raw_message: str) -> tuple[str, bool]:
    if isinstance(message, str):
        return message, _cq_at_matches(message, self_id)
    if not isinstance(message, list):
        return raw_message, _cq_at_matches(raw_message, self_id)
    parts: list[str] = []
    is_at = False
    for segment in message:
        if not isinstance(segment, dict):
            continue
        seg_type = segment.get("type")
        data = segment.get("data") or {}
        if seg_type == "text":
            parts.append(str(data.get("text") or ""))
        elif seg_type == "at":
            if str(data.get("qq") or "") == self_id:
                is_at = True
    return "".join(parts), is_at


def _cq_at_matches(text: str, self_id: str) -> bool:
    return any(qq == self_id for qq in re.findall(r"\[CQ:at,qq=(\d+)\]", text))
