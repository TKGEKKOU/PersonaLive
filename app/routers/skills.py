"""技能（Skill）管理 API：前端"插件"页的技能区块后端。"""

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from agents.skills import create_skill, delete_skill, list_skills
from agents.registry import tool_specs


router = APIRouter(prefix="/api/skills", tags=["skills"])


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
    return [_to_dict(spec) for spec in list_skills()]


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
        spec = create_skill(
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
        delete_skill(name)
    except ValueError as exc:
        # 内置技能受保护，不允许删除。
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError:
        raise HTTPException(status_code=404, detail="Skill not found") from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
