"""Voice assets backed by trained GPT-SoVITS models.

Assets live under ``data/gpt_sovits/voices/<id>/`` inside the project:
``*.ckpt`` (GPT weights), ``*.pth`` (SoVITS weights), optional reference
audio and dataset files. Import copies external models into the project so
the project stays self-contained.
"""

import shutil
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi import File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import VoiceAsset
from app.routers.settings import require_local
from persona.service import LOCAL_WORKSPACE_ID
from voice.gpt_sovits import GPTSoVITSAdapter, GPTSoVITSConfig, GPTSoVITSNotInstalled
from voice.gpt_sovits.config import detect_install_dir
from voice.gpt_sovits.training import (
    ASSET_STATUS_READY,
    TrainingService,
)


router = APIRouter(prefix="/api", tags=["voice-assets"])


class VoiceAssetCreate(BaseModel):
    name: str
    engine: str = "gpt_sovits"


class VoiceAssetUpdate(BaseModel):
    name: str | None = None
    gpt_weights_path: str | None = None
    sovits_weights_path: str | None = None
    refer_audio_path: str | None = None


class VoiceAssetImport(BaseModel):
    directory: str


class VoiceAssetSynthesize(BaseModel):
    text: str
    text_lang: str = "zh"


class VoiceAssetTrainFromStudio(BaseModel):
    name: str
    session_id: str
    segment_indices: list[int] | None = None
    language: str = "zh"


class GPTSoVITSConfigUpdate(BaseModel):
    install_dir: str | None = None
    api_port: int | None = None
    download_url: str | None = None


class GPTSoVITSInstallRequest(BaseModel):
    url: str


def protected(request: Request, header: str) -> None:
    require_local(request)
    if header != "web":
        raise HTTPException(status_code=403, detail="Missing same-origin request header")


def asset_response(asset: VoiceAsset) -> dict:
    data = {
        "id": asset.id,
        "name": asset.name,
        "engine": asset.engine,
        "status": asset.status,
        "training_stage": asset.training_stage,
        "gpt_weights_path": asset.gpt_weights_path,
        "sovits_weights_path": asset.sovits_weights_path,
        "refer_audio_path": asset.refer_audio_path,
        "dataset_dir": asset.dataset_dir,
        "preview_audio_path": asset.preview_audio_path,
        "error_message": asset.error_message,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
    }
    return data


def asset_reference_prompt(asset: VoiceAsset) -> tuple[str, str]:
    """Return (refer_audio_path, prompt_text) for the asset's reference
    segment by looking up the training .list transcript."""

    refer = asset.refer_audio_path or ""
    prompt = ""
    if not refer or not asset.dataset_dir:
        return refer, prompt
    dataset_dir = Path(asset.dataset_dir)
    list_files = list(dataset_dir.glob("*.list"))
    if not list_files:
        return refer, prompt
    refer_name = Path(refer).name
    for line in list_files[0].read_text(encoding="utf-8").splitlines():
        parts = line.split("|")
        if len(parts) >= 4 and Path(parts[0]).name == refer_name:
            prompt = parts[3].strip()
            break
    return refer, prompt


def asset_response_with_progress(asset: VoiceAsset) -> dict:
    """Include live training progress parsed from the asset's training log."""

    data = asset_response(asset)
    progress = None
    if asset.status == "processing" and asset.dataset_dir:
        log_path = Path(asset.dataset_dir).parent / "training.log"
        if log_path.is_file():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            lines = [line for line in text.splitlines() if line.strip()][-400:]
            epoch = None
            for line in reversed(lines):
                match = re.search(r"====> Epoch: (\d+)", line)
                if match:
                    epoch = int(match.group(1))
                    break
                match = re.search(r"Epoch (\d+):", line)
                if match:
                    epoch = int(match.group(1)) + 1
                    break
            stage = asset.training_stage or ""
            total = 100 if "SoVITS" in stage or "s2" in stage.lower() else 20
            progress = {"epoch": epoch, "total_epochs": total}
    data["training_progress"] = progress
    return data


@router.get("/voice-assets")
def list_assets(request: Request, session: Session = Depends(get_session)):
    require_local(request)
    assets = session.query(VoiceAsset).order_by(VoiceAsset.created_at.desc()).all()
    return {"items": [asset_response(a) for a in assets]}


@router.post("/voice-assets", status_code=201)
def create_asset(
    payload: VoiceAssetCreate,
    request: Request,
    session: Session = Depends(get_session),
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="名称不能为空")
    asset = VoiceAsset(name=name, engine=payload.engine, workspace_id=LOCAL_WORKSPACE_ID)
    session.add(asset)
    session.commit()
    session.refresh(asset)
    return asset_response(asset)


@router.get("/voice-assets/{asset_id}")
def get_asset(
    asset_id: str,
    request: Request,
    session: Session = Depends(get_session),
):
    require_local(request)
    asset = session.get(VoiceAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="音色不存在")
    return asset_response_with_progress(asset)


@router.patch("/voice-assets/{asset_id}")
def update_asset(
    asset_id: str,
    payload: VoiceAssetUpdate,
    request: Request,
    session: Session = Depends(get_session),
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    asset = session.get(VoiceAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="音色不存在")
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(asset, key, value)
    session.commit()
    session.refresh(asset)
    return asset_response(asset)


@router.delete("/voice-assets/{asset_id}")
def delete_asset(
    asset_id: str,
    request: Request,
    session: Session = Depends(get_session),
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    asset = session.get(VoiceAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="音色不存在")
    session.delete(asset)
    session.commit()
    return {"ok": True}


def _import_asset_from_directory(project_root: Path, directory: Path, session: Session) -> list[VoiceAsset]:
    """Scan a directory tree for GPT-SoVITS model pairs (ckpt + pth) and
    register each pair as a VoiceAsset, copying the model files into the
    project's voice asset directory."""

    directory = directory.resolve()
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail=f"目录不存在：{directory}")
    pairs: list[tuple[Path, Path, str]] = []
    for ckpt in directory.rglob("*.ckpt"):
        siblings = list(ckpt.parent.glob("*.pth"))
        for pth in siblings:
            name = f"{ckpt.parent.name or ckpt.stem} · {ckpt.stem}"
            pairs.append((ckpt, pth, name))
    imported: list[VoiceAsset] = []
    for ckpt, pth, name in pairs:
        existing = (
            session.query(VoiceAsset)
            .filter(
                VoiceAsset.gpt_weights_path == str(ckpt),
                VoiceAsset.sovits_weights_path == str(pth),
            )
            .first()
        )
        if existing:
            continue
        wavs = sorted(ckpt.parent.glob("*.wav"))
        asset = VoiceAsset(
            name=name,
            engine="gpt_sovits",
            workspace_id=LOCAL_WORKSPACE_ID,
            status=ASSET_STATUS_READY,
            training_stage="已导入",
        )
        session.add(asset)
        session.flush()
        target_dir = project_root / "data" / "gpt_sovits" / "voices" / asset.id
        target_dir.mkdir(parents=True, exist_ok=True)
        new_ckpt = target_dir / ckpt.name
        new_pth = target_dir / pth.name
        shutil.copy2(ckpt, new_ckpt)
        shutil.copy2(pth, new_pth)
        refer = None
        if wavs:
            refer = target_dir / wavs[0].name
            shutil.copy2(wavs[0], refer)
        lists = sorted(ckpt.parent.glob("*.list"))
        if lists:
            shutil.copy2(lists[0], target_dir / lists[0].name)
        asset.gpt_weights_path = str(new_ckpt)
        asset.sovits_weights_path = str(new_pth)
        asset.refer_audio_path = str(refer) if refer else None
        imported.append(asset)
    session.commit()
    for asset in imported:
        session.refresh(asset)
    return imported


@router.post("/voice-assets/import")
def import_assets(
    payload: VoiceAssetImport,
    request: Request,
    session: Session = Depends(get_session),
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    project_root = Path(request.app.state.tts_resources.project_root)
    imported = _import_asset_from_directory(project_root, Path(payload.directory), session)
    return {"imported": [asset_response(a) for a in imported]}


@router.post("/voice-assets/{asset_id}/synthesize")
def synthesize_asset(
    asset_id: str,
    payload: VoiceAssetSynthesize,
    request: Request,
    session: Session = Depends(get_session),
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    asset = session.get(VoiceAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="音色不存在")
    if not asset.gpt_weights_path or not asset.sovits_weights_path:
        raise HTTPException(status_code=409, detail="音色模型尚未就绪")
    if not payload.text.strip():
        raise HTTPException(status_code=422, detail="文本不能为空")
    adapter: GPTSoVITSAdapter = request.app.state.gpt_sovits
    refer_audio, prompt_text = asset_reference_prompt(asset)
    try:
        audio = adapter.synthesize(
            payload.text.strip(),
            text_lang=payload.text_lang,
            gpt_weights=asset.gpt_weights_path,
            sovits_weights=asset.sovits_weights_path,
            refer_audio=refer_audio,
            prompt_text=prompt_text,
            prompt_lang=payload.text_lang,
        )
    except GPTSoVITSNotInstalled as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return Response(content=audio, media_type="audio/wav")


@router.post("/voice-assets/{asset_id}/train")
async def start_training(
    asset_id: str,
    request: Request,
    session: Session = Depends(get_session),
    files: list[UploadFile] = File(default=[]),
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    asset = session.get(VoiceAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="音色不存在")
    training: TrainingService = request.app.state.gpt_sovits_training
    audio_paths: list[str] = []
    if files:
        raw_dir = training.dataset_dir(asset.id) / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        for index, upload in enumerate(files, start=1):
            suffix = Path(upload.filename or f"audio{index}").suffix.lower() or ".wav"
            target = raw_dir / f"{index:03d}{suffix}"
            target.write_bytes(await upload.read())
            audio_paths.append(str(target))
        training.prepare_dataset(asset.id, audio_paths)
        training.label_with_asr(asset.id)
    if not training.start_training(asset.id):
        raise HTTPException(status_code=409, detail="已有训练任务在进行")
    return {"ok": True, "status": "processing", "samples": len(audio_paths)}


@router.post("/voice-assets/train-from-studio")
def train_from_studio(
    payload: VoiceAssetTrainFromStudio,
    request: Request,
    session: Session = Depends(get_session),
    x_yumeno_request: str = Header(default=""),
):
    """Train a voice asset from segments already sliced in the voice studio."""

    protected(request, x_yumeno_request)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="名称不能为空")
    studio = request.app.state.voice_studio
    session_dir = studio.sessions_dir / payload.session_id
    meta_path = session_dir / "meta.json"
    if not meta_path.is_file():
        raise HTTPException(status_code=404, detail="声音工坊会话不存在")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="会话数据损坏") from exc
    segments = meta.get("segments") or []
    if payload.segment_indices is not None:
        wanted = set(payload.segment_indices)
        segments = [s for s in segments if s.get("index") in wanted]
    if not segments:
        raise HTTPException(status_code=422, detail="没有可用的音频片段")
    paths = [str(session_dir / "segments" / s["file"]) for s in segments]

    training: TrainingService = request.app.state.gpt_sovits_training
    asset = VoiceAsset(
        name=name,
        engine="gpt_sovits",
        workspace_id=LOCAL_WORKSPACE_ID,
    )
    session.add(asset)
    session.commit()
    session.refresh(asset)
    try:
        training.prepare_dataset(asset.id, paths)
        training.label_with_asr(asset.id, language=payload.language)
    except Exception as exc:
        session.delete(asset)
        session.commit()
        raise HTTPException(status_code=500, detail=f"数据准备失败：{exc}") from exc
    if not training.start_training(asset.id):
        raise HTTPException(status_code=409, detail="已有训练任务在进行")
    return {"ok": True, "asset": asset_response(asset)}


# ----------------------------------------------------------------------
# GPT-SoVITS engine configuration
# ----------------------------------------------------------------------


@router.get("/gpt-sovits/status")
def gpt_sovits_status(request: Request):
    require_local(request)
    status = request.app.state.gpt_sovits.status()
    status["install"] = request.app.state.gpt_sovits_install.status()
    return status


@router.patch("/gpt-sovits/config")
def update_gpt_sovits_config(
    payload: GPTSoVITSConfigUpdate,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    config: GPTSoVITSConfig = request.app.state.gpt_sovits_config
    config.save(
        install_dir=payload.install_dir,
        api_port=payload.api_port,
        download_url=payload.download_url,
    )
    return request.app.state.gpt_sovits.status()


@router.post("/gpt-sovits/detect")
def detect_gpt_sovits(
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    config: GPTSoVITSConfig = request.app.state.gpt_sovits_config
    found = detect_install_dir()
    if found:
        config.save(install_dir=str(found))
    return request.app.state.gpt_sovits.status()


@router.post("/gpt-sovits/install", status_code=202)
def install_gpt_sovits(
    payload: GPTSoVITSInstallRequest,
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    try:
        started = request.app.state.gpt_sovits_install.start_install(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not started:
        raise HTTPException(status_code=409, detail="GPT-SoVITS 安装已在进行中")
    return request.app.state.gpt_sovits_install.status()


@router.delete("/gpt-sovits/install/cancel", status_code=202)
def cancel_gpt_sovits_install(
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    request.app.state.gpt_sovits_install.cancel_install()
    return request.app.state.gpt_sovits_install.status()


@router.delete("/gpt-sovits/install")
def remove_gpt_sovits_install(
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    request.app.state.gpt_sovits.stop_service()
    request.app.state.gpt_sovits_install.remove_install()
    return request.app.state.gpt_sovits_install.status()


@router.post("/gpt-sovits/service/start")
def start_gpt_sovits_service(
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    try:
        request.app.state.gpt_sovits.ensure_service()
    except GPTSoVITSNotInstalled as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return request.app.state.gpt_sovits.status()


@router.post("/gpt-sovits/service/stop")
def stop_gpt_sovits_service(
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    request.app.state.gpt_sovits.stop_service()
    return request.app.state.gpt_sovits.status()


@router.post("/gpt-sovits/model-directory")
def open_gpt_sovits_directory(
    request: Request,
    x_yumeno_request: str = Header(default=""),
):
    protected(request, x_yumeno_request)
    from voice.resource_directory import open_resource_directory

    install_dir = request.app.state.gpt_sovits_install.install_dir
    return {"opened_directory": open_resource_directory(install_dir)}
