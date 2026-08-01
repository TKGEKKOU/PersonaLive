from pathlib import Path

from extensions.storage import read_json, write_json


ONEBOT_DEFAULTS = {
    "enabled": False,
    "access_token": "",
    "group_trigger": "at",
    "prefix": "",
    "default_persona_id": "",
}


def load_integrations(path: Path) -> dict:
    return read_json(path)


def save_integrations(path: Path, data: dict) -> None:
    write_json(path, data)


def onebot_config(data: dict) -> dict:
    raw = data.get("onebot11") or {}
    config = dict(ONEBOT_DEFAULTS)
    config.update({key: raw.get(key, default) for key, default in ONEBOT_DEFAULTS.items()})
    if config["group_trigger"] not in {"at", "prefix"}:
        config["group_trigger"] = "at"
    return config


def onebot_runtime_config(project_root: Path) -> dict:
    return onebot_config(load_integrations(project_root / "data" / "integrations.json"))
