"""应用内置 Embedding 模型资源 API。"""

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from app.routers.asr import protected


router = APIRouter(prefix="/api/embedding", tags=["embedding"])


class EmbeddingResourceConfig(BaseModel):
    model_id: str
    source: str = "modelscope"
    device: str = "auto"


def manager(request: Request):
    return request.app.state.embedding_resources


@router.get("/status")
def get_status(request: Request):
    from app.routers.settings import require_local
    require_local(request)
    return manager(request).status()


@router.patch("/config")
def configure(payload: EmbeddingResourceConfig, request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    try:
        return manager(request).configure(payload.model_id, payload.source, payload.device)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/install", status_code=status.HTTP_202_ACCEPTED)
def install(payload: EmbeddingResourceConfig, request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    try:
        manager(request).start_install(payload.model_id, payload.source, payload.device)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return manager(request).status()


@router.delete("/install/cancel", status_code=status.HTTP_202_ACCEPTED)
def cancel(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    manager(request).cancel_install()
    return manager(request).status()


@router.delete("/model")
def remove(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    try:
        return manager(request).remove_model()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/model-directory")
def open_model_directory(request: Request, x_yumeno_request: str = Header(default="")):
    protected(request, x_yumeno_request)
    return manager(request).open_model_directory()
