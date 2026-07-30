from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from app.routers.settings import require_local


router = APIRouter(prefix="/api/asr", tags=["asr"])


class ASRConfigUpdate(BaseModel):
    enabled: bool | None = None
    python_path: str | None = None
    model_path: str | None = None
    ffmpeg_path: str | None = None


def protected(request: Request, header: str) -> None:
    require_local(request)
    if header != "web":
        raise HTTPException(status_code=403, detail="Missing same-origin request header")


@router.get("/status")
def get_status(request: Request):
    require_local(request)
    return request.app.state.asr_resources.status()


@router.patch("/config")
def update_config(payload: ASRConfigUpdate, request: Request, x_personalive_request: str = Header(default="")):
    protected(request, x_personalive_request)
    return request.app.state.asr_resources.configure(**payload.model_dump(exclude_unset=True))


@router.post("/install", status_code=status.HTTP_202_ACCEPTED)
def install(request: Request, x_personalive_request: str = Header(default="")):
    protected(request, x_personalive_request)
    request.app.state.asr_resources.start_install()
    return request.app.state.asr_resources.status()


@router.delete("/install")
def remove(request: Request, x_personalive_request: str = Header(default="")):
    protected(request, x_personalive_request)
    return request.app.state.asr_resources.remove_managed()
