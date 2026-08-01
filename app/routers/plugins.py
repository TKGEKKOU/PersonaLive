from fastapi import APIRouter, HTTPException, Request

from app.routers.settings import require_local
from app.schemas import PluginConfigPayload, PluginEnablePayload, PluginInfoResponse


router = APIRouter(prefix="/api/plugins", tags=["plugins"])


def _manager(request: Request):
    return request.app.state.plugin_manager


@router.get("", response_model=list[PluginInfoResponse])
def list_plugins(request: Request) -> list[PluginInfoResponse]:
    require_local(request)
    return [_to_response(item) for item in _manager(request).list_plugins()]


@router.put("/{name}", response_model=PluginInfoResponse)
def set_plugin_enabled(name: str, payload: PluginEnablePayload, request: Request) -> PluginInfoResponse:
    require_local(request)
    manager = _manager(request)
    try:
        info = manager.enable(name) if payload.enabled else manager.disable(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Plugin not found") from exc
    return _to_response(info)


@router.put("/{name}/config", response_model=PluginInfoResponse)
def update_plugin_config(name: str, payload: PluginConfigPayload, request: Request) -> PluginInfoResponse:
    require_local(request)
    try:
        info = _manager(request).save_config(name, payload.config)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Plugin not found") from exc
    return _to_response(info)


def _to_response(info) -> PluginInfoResponse:
    return PluginInfoResponse(
        name=info.name,
        version=info.version,
        description=info.description,
        author=info.author,
        enabled=info.enabled,
        config=info.config,
        error=info.error,
    )
