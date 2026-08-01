from pathlib import Path

from extensions.storage import read_json, write_json


def load_bindings(path: Path) -> dict:
    return read_json(path)


def save_bindings(path: Path, data: dict) -> None:
    write_json(path, data)


def persona_for(
    bindings: dict,
    chat_type: str,
    chat_id: str,
    default_persona_id: str,
) -> str | None:
    bound = (bindings.get(chat_type) or {}).get(chat_id)
    return bound or default_persona_id or None


def bind_persona(bindings: dict, chat_type: str, chat_id: str, persona_id: str) -> None:
    bindings.setdefault(chat_type, {})[chat_id] = persona_id
