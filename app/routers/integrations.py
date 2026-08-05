from fastapi import APIRouter, Request

from app.routers.settings import require_local
from app.schemas import OneBotConfigUpdate, QqOfficialConfigUpdate
from integrations.config import load_integrations, onebot_config, save_integrations
from integrations.qq_official.config import qq_official_config
from settings import Settings


router = APIRouter(prefix="/api/integrations", tags=["integrations"])
INTEGRATIONS_PATH = Settings.load().project_root / "data" / "integrations.json"


def _onebot_manager(request: Request):
    return getattr(request.app.state, "onebot", None)


def _qq_official_manager(request: Request):
    return getattr(request.app.state, "qq_official", None)


def _onebot_response(request: Request) -> dict:
    config = onebot_config(load_integrations(INTEGRATIONS_PATH))
    manager = _onebot_manager(request)
    status = manager.status() if manager is not None else {
        "connected": False, "client_count": 0, "error": None
    }
    return {
        "enabled": config["enabled"],
        "access_token_configured": bool(config["access_token"]),
        "group_trigger": config["group_trigger"],
        "prefix": config["prefix"],
        "default_persona_id": config["default_persona_id"],
        "ws_path": "/api/onebot/ws",
        "connected": status.get("connected", False),
        "client_count": status.get("client_count", 0),
        "error": status.get("error"),
    }


def _qq_official_response(request: Request) -> dict:
    config = qq_official_config(load_integrations(INTEGRATIONS_PATH))
    manager = _qq_official_manager(request)
    status = manager.status() if manager is not None else {
        "enabled": config["enabled"],
        "configured": bool(config["appid"]) and bool(config["secret"]),
        "sandbox": config["sandbox"],
        "connected": False,
        "error": None,
        "bot_openid": None,
    }
    return {
        "enabled": config["enabled"],
        "appid": config["appid"],
        "secret_configured": bool(config["secret"]),
        "sandbox": config["sandbox"],
        "group_trigger": config["group_trigger"],
        "prefix": config["prefix"],
        "default_persona_id": config["default_persona_id"],
        "connected": status.get("connected", False),
        "error": status.get("error"),
        "bot_openid": status.get("bot_openid"),
    }


@router.get("")
def get_integrations(request: Request) -> dict:
    require_local(request)
    return {
        "onebot11": _onebot_response(request),
        "qq_official": _qq_official_response(request),
    }


@router.put("/onebot11")
def update_onebot(payload: OneBotConfigUpdate, request: Request) -> dict:
    require_local(request)
    data = load_integrations(INTEGRATIONS_PATH)
    current = onebot_config(data)
    updates = payload.model_dump(exclude_unset=True)
    current.update({key: value for key, value in updates.items() if value is not None})
    data["onebot11"] = current
    save_integrations(INTEGRATIONS_PATH, data)
    manager = _onebot_manager(request)
    if manager is not None:
        manager.config_changed(current)
    return _onebot_response(request)


@router.put("/qq_official")
def update_qq_official(payload: QqOfficialConfigUpdate, request: Request) -> dict:
    require_local(request)
    data = load_integrations(INTEGRATIONS_PATH)
    current = qq_official_config(data)
    updates = payload.model_dump(exclude_unset=True)
    # secret 留空表示不修改（避免前端回显明文）
    if updates.get("secret") in (None, ""):
        updates.pop("secret", None)
    current.update({key: value for key, value in updates.items() if value is not None})
    data["qq_official"] = current
    save_integrations(INTEGRATIONS_PATH, data)
    manager = _qq_official_manager(request)
    if manager is not None:
        manager.config_changed()
    return _qq_official_response(request)
