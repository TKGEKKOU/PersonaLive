import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request

from app.schemas import LocalSettingsResponse, LocalSettingsUpdate
from settings import SUPPORTED_ASR_PROVIDERS, SUPPORTED_WEB_SEARCH_PROVIDERS, Settings


router = APIRouter(prefix="/api/settings", tags=["settings"])
SETTINGS_PATH = Settings.load().project_root / "data" / "local_settings.json"
LOCAL_CLIENT_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
LOCAL_REQUEST_HOSTS = {"127.0.0.1", "::1", "localhost"}


def effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    return {"http": 80, "https": 443}.get(scheme.lower())


def require_local(request: Request) -> None:
    client_host = request.client.host if request.client else ""
    if client_host not in LOCAL_CLIENT_HOSTS:
        raise HTTPException(status_code=403, detail="Local settings are available on localhost only")
    try:
        request_host = urlsplit(f"//{request.headers.get('host', '')}")
        request_hostname = (request_host.hostname or "").lower()
        request_port = effective_port(request.url.scheme, request_host.port)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Local settings are available on localhost only") from exc
    if request_hostname not in LOCAL_REQUEST_HOSTS:
        raise HTTPException(status_code=403, detail="Local settings are available on localhost only")

    origin = request.headers.get("origin")
    if not origin:
        return
    try:
        parsed_origin = urlsplit(origin)
        origin_hostname = (parsed_origin.hostname or "").lower()
        origin_port = effective_port(parsed_origin.scheme, parsed_origin.port)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Local settings are available on localhost only") from exc
    if (
        parsed_origin.scheme.lower() != request.url.scheme.lower()
        or origin_hostname not in LOCAL_REQUEST_HOSTS
        or origin_port != request_port
    ):
        raise HTTPException(status_code=403, detail="Local settings are available on localhost only")


def read_settings(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="Local settings file is invalid") from exc
    return value if isinstance(value, dict) else {}


def settings_response(path: Path, restart_required: bool = False) -> LocalSettingsResponse:
    values = read_settings(path)
    legacy_enabled = bool(values.get("enable_web_fallback", False))
    web_search_provider = str(values.get("web_search_provider") or "") or ("tavily" if legacy_enabled else "off")
    if web_search_provider not in SUPPORTED_WEB_SEARCH_PROVIDERS:
        web_search_provider = "off"
    web_search_api_key = str(values.get("web_search_api_key") or values.get("tavily_api_key") or "")
    asr_provider = str(values.get("asr_provider") or "off")
    if asr_provider not in SUPPORTED_ASR_PROVIDERS:
        asr_provider = "off"
    return LocalSettingsResponse(
        openai_api_key_configured=bool(values.get("openai_api_key")),
        openai_base_url=str(values.get("openai_base_url") or ""),
        openai_model=str(values.get("openai_model") or ""),
        embedding_api_key_configured=bool(values.get("embedding_api_key")),
        embedding_base_url=str(values.get("embedding_base_url") or ""),
        embedding_model=str(values.get("embedding_model") or ""),
        embedding_dimensions=int(values.get("embedding_dimensions") or 512),
        embedding_send_dimensions=bool(values.get("embedding_send_dimensions", True)),
        web_search_provider=web_search_provider,
        web_search_api_key_configured=bool(web_search_api_key),
        web_search_base_url=str(values.get("web_search_base_url") or ""),
        enable_web_fallback=web_search_provider != "off",
        asr_provider=asr_provider,
        asr_api_key_configured=bool(values.get("asr_api_key")),
        asr_base_url=str(values.get("asr_base_url") or ""),
        asr_model=str(values.get("asr_model") or ""),
        asr_language=str(values.get("asr_language") or ""),
        restart_required=restart_required,
    )


def update_local_settings(path: Path, updates: dict) -> None:
    values = read_settings(path)
    values.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".settings.", suffix=".tmp", delete=False
    ) as temporary:
        json.dump(values, temporary, ensure_ascii=False, indent=2)
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def delete_local_settings(path: Path) -> None:
    if path.is_file():
        path.unlink()


@router.get("", response_model=LocalSettingsResponse)
def get_settings(request: Request) -> LocalSettingsResponse:
    require_local(request)
    return settings_response(SETTINGS_PATH)


@router.patch("", response_model=LocalSettingsResponse)
def save_settings(payload: LocalSettingsUpdate, request: Request) -> LocalSettingsResponse:
    require_local(request)
    submitted = payload.model_dump(exclude_none=True)
    provider = submitted.get("web_search_provider")
    if provider is not None and provider not in SUPPORTED_WEB_SEARCH_PROVIDERS:
        raise HTTPException(status_code=422, detail="Unsupported web search provider")
    asr_provider = submitted.get("asr_provider")
    if asr_provider is not None and asr_provider not in SUPPORTED_ASR_PROVIDERS:
        raise HTTPException(status_code=422, detail="Unsupported ASR provider")
    if "web_search_api_key" not in submitted and submitted.get("tavily_api_key"):
        submitted["web_search_api_key"] = submitted["tavily_api_key"]
    if provider is None and submitted.get("tavily_api_key") and submitted.get("enable_web_fallback"):
        submitted["web_search_provider"] = "tavily"
        provider = "tavily"
    if provider is None and submitted.get("enable_web_fallback") is False:
        submitted["web_search_provider"] = "off"
        provider = "off"
    updates = {}
    for field, value in submitted.items():
        if isinstance(value, bool) or isinstance(value, int):
            updates[field] = value
        elif value.strip():
            updates[field] = value.strip()
    updates.pop("tavily_api_key", None)
    if provider is not None:
        updates["enable_web_fallback"] = provider != "off"
    if updates:
        update_local_settings(SETTINGS_PATH, updates)
    return settings_response(SETTINGS_PATH, restart_required=False)


@router.delete("", response_model=LocalSettingsResponse)
def reset_settings(request: Request) -> LocalSettingsResponse:
    require_local(request)
    delete_local_settings(SETTINGS_PATH)
    return settings_response(SETTINGS_PATH, restart_required=False)
