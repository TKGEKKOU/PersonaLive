"""技能（Skill）管理 API：前端"插件"页的技能区块后端。"""

import io
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from agents import skills as skills_module
from agents.skill_parser import parse_skill_dir
from agents.registry import tool_specs


router = APIRouter(prefix="/api/skills", tags=["skills"])

MAX_ZIP_BYTES = 25 * 1024 * 1024
MAX_EXTRACT_BYTES = 25 * 1024 * 1024
MAX_FILES = 500


class SkillCreate(BaseModel):
    name: str = Field(..., description="技能名，匹配 [a-z0-9_-]+")
    instructions: str = Field(..., description="加载后注入 system prompt 的提示词正文")
    description: str = ""
    prompt_hint: str = ""
    tool_names: list[str] = []


def _to_dict(spec) -> dict:
    return {
        "name": spec.name,
        "description": spec.description,
        "instructions": spec.instructions,
        "prompt_hint": spec.prompt_hint,
        "tool_names": list(spec.tool_names),
        "builtin": spec.builtin,
        "format": spec.format,
        "metadata": spec.metadata,
    }


@router.get("")
def list_skills_api() -> list[dict]:
    return [_to_dict(spec) for spec in skills_module.list_skills()]


@router.get("/tools")
def list_skill_tools_api() -> list[dict]:
    """技能可引用的工具清单（供前端新增技能时勾选）；MCP 工具注册进
    ToolSpec 表后会自动出现在这里，无需改前端。"""

    return [
        {
            "name": spec.name,
            "mutates_data": spec.mutates_data,
            "requires_confirmation": spec.requires_confirmation,
        }
        for spec in tool_specs()
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_skill_api(payload: SkillCreate) -> dict:
    try:
        spec = skills_module.create_skill(
            name=payload.name,
            instructions=payload.instructions,
            description=payload.description,
            prompt_hint=payload.prompt_hint,
            tool_names=tuple(payload.tool_names),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_dict(spec)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_skill_api(name: str) -> Response:
    try:
        skills_module.delete_skill(name)
    except ValueError as exc:
        # 内置技能受保护，不允许删除。
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail="Skill not found") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/upload")
async def upload_skills_api(file: UploadFile = File(...)) -> dict:
    """上传标准技能包 zip（一个或多个含 SKILL.md 的技能目录）。"""

    data = await file.read()
    if len(data) > MAX_ZIP_BYTES:
        raise HTTPException(status_code=400, detail="技能包 zip 超过 25MB 上限")
    installed: list[str] = []
    skipped: list[dict] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_FILES:
                raise HTTPException(status_code=400, detail=f"技能包文件数超过 {MAX_FILES} 上限")
            total = 0
            for info in infos:
                if info.is_dir():
                    continue
                parts = info.filename.replace("\\", "/").split("/")
                if info.filename.startswith("/") or ".." in parts:
                    raise HTTPException(status_code=400, detail=f"非法路径: {info.filename}")
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise HTTPException(status_code=400, detail=f"不支持符号链接: {info.filename}")
                total += info.file_size
            if total > MAX_EXTRACT_BYTES:
                raise HTTPException(status_code=400, detail="解压后超过 25MB 上限")
            with tempfile.TemporaryDirectory() as tmp:
                tmp_root = Path(tmp)
                archive.extractall(tmp)
                candidates: list[Path] = []
                if (tmp_root / "SKILL.md").is_file():
                    candidates.append(tmp_root)
                candidates.extend(
                    child
                    for child in sorted(tmp_root.iterdir())
                    if child.is_dir() and (child / "SKILL.md").is_file()
                )
                known = {spec.name for spec in tool_specs()}
                for skill_dir in candidates:
                    parsed = parse_skill_dir(skill_dir)
                    if parsed is None:
                        skipped.append({"name": skill_dir.name, "reason": "SKILL.md 解析/校验失败"})
                        continue
                    name = parsed["name"]
                    unknown = [t for t in parsed["tool_names"] if t not in known]
                    if unknown:
                        skipped.append({"name": name, "reason": f"未知工具: {', '.join(unknown)}"})
                        continue
                    existing = {spec.name: spec for spec in skills_module.list_skills()}
                    if name in existing:
                        if existing[name].builtin:
                            skipped.append({"name": name, "reason": "与内置技能同名，不可覆盖"})
                            continue
                        raise HTTPException(status_code=409, detail=f"技能 {name} 已存在，请先删除再上传")
                    target = skills_module.USER_SKILL_DIR / name
                    shutil.copytree(skill_dir, target)
                    installed.append(name)
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="不是有效的 zip 文件")
    if installed:
        skills_module.refresh_skills()
    return {"installed": installed, "skipped": skipped}
