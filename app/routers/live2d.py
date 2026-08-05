from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from app.routers.settings import require_local
from settings import Settings


router = APIRouter(prefix="/api/live2d", tags=["live2d"])

LIVE2D_ROOT = Settings.load().project_root / "data" / "live2d"
CUBISM4_GLOB = "*.model3.json"
CUBISM2_GLOB = "*.model.json"


def discover_models(root: Path) -> list[dict]:
    """Scan one directory per model. Prefer Cubism 4 (.model3.json) over
    Cubism 2 (.model.json) when both exist in the same folder."""
    if not root.is_dir():
        return []
    models: list[dict] = []
    for directory in sorted((item for item in root.iterdir() if item.is_dir())):
        cubism4 = sorted(directory.glob(CUBISM4_GLOB))
        cubism2 = sorted(directory.glob(CUBISM2_GLOB))
        if cubism4:
            entry, kind = cubism4[0], "cubism4"
        elif cubism2:
            entry, kind = cubism2[0], "cubism2"
        else:
            continue
        models.append(
            {
                "id": directory.name,
                "name": directory.name,
                "entry": f"{directory.name}/{entry.name}",
                "kind": kind,
            }
        )
    return models


@router.get("/models")
def list_models(request: Request) -> dict:
    require_local(request)
    return {"models": discover_models(LIVE2D_ROOT)}
