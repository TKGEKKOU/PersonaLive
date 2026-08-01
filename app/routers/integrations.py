from fastapi import APIRouter, Request

from app.routers.settings import require_local
from app.schemas import OneBotConfigUpdate
from integrations.config import load_integrations, onebot_config, save_integrations
from settings import Settings


router = APIRouter(prefix="/api/integrations", tags=["integrations"])
INTEGRATIONS_PATH = Settings.load().project_root / "data" / "integrations.json"


def _manager(request: Request):
    return getattr(request.app.state, "onebot", None)


def _onebot_response(request: Request) -> dict:
    config = onebot_config(load_integrations(INTEGRATIONS_PATH))
    manager = _manager(request)
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


@router.get("")
def get_integrations(request: Request) -> dict:
    require_local(request)
    return {"onebot11": _onebot_response(request)}


@router.put("/onebot11")
def update_onebot(payload: OneBotConfigUpdate, request: Request) -> dict:
    require_local(request)
    data = load_integrations(INTEGRATIONS_PATH)
    current = onebot_config(data)
    updates = payload.model_dump(exclude_unset=True)
    current.update({key: value for key, value in updates.items() if value is not None})
    data["onebot11"] = current
    save_integrations(INTEGRATIONS_PATH, data)
    manager = _manager(request)
    if manager is not None:
        manager.config_changed(current)
    return _onebot_response(request)
