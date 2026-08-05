from pathlib import Path

from extensions.storage import read_json
from integrations.config import load_integrations


QQ_OFFICIAL_DEFAULTS = {
    "enabled": False,
    "appid": "",
    "secret": "",
    "sandbox": True,
    "group_trigger": "at",
    "prefix": "",
    "default_persona_id": "",
}


def qq_official_config(data: dict) -> dict:
    raw = data.get("qq_official") or {}
    config = dict(QQ_OFFICIAL_DEFAULTS)
    config.update({key: raw.get(key, default) for key, default in QQ_OFFICIAL_DEFAULTS.items()})
    if config["group_trigger"] not in {"at", "prefix"}:
        config["group_trigger"] = "at"
    return config


def qq_official_runtime_config(project_root: Path) -> dict:
    return qq_official_config(load_integrations(project_root / "data" / "integrations.json"))
