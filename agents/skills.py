"""Agent Skills 动态技能包。

Skill = 提示词包（instructions）+ 可选工具集（tool_names），解决"工具过载"与
"行为注入"两类问题：
- 提示词：加载技能后由 skill_middleware 拼进 Supervisor 的 system prompt，
  让模型获得该技能的领域行为约束（真正意义上的"提示词插件"）；
- 工具：tool_names 引用 agents.registry 的 ToolSpec.name，只有加载该技能后
  工具才对模型可见。未来 MCP 工具只要注册进 ToolSpec 表，即可被技能引用，
  这是为 MCP 预留的天然扩展点。

技能来源：内置（agents/skills/，随代码分发，只读）＋ 自定义
（data/skills/，可由前端"插件"页增删，无需改代码）。
"""

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from langgraph.types import Command

from agents.context import PersonaAgentContext
from agents.mcp_grants import is_mcp_tool_visible
from agents.registry import tool_specs
from settings import Settings


SKILL_DIR = Path(__file__).resolve().parent / "skills"
USER_SKILL_DIR = Settings.load().project_root / "data" / "skills"
NAME_PATTERN = re.compile(r"[a-z0-9_-]+")


@dataclass(frozen=True)
class SkillSpec:
    """技能元数据；instructions 是加载后注入的提示词正文，tool_names 必须引用
    agents.registry 中已注册的 ToolSpec.name。"""

    name: str
    description: str
    instructions: str
    tool_names: tuple[str, ...]
    prompt_hint: str = ""
    builtin: bool = False


def _scan_dir(directory: Path, known: set[str], builtin: bool) -> dict[str, SkillSpec]:
    loaded: dict[str, SkillSpec] = {}
    if not directory.is_dir():
        return loaded
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        name = str(data.get("name") or "").strip()
        tool_names = tuple(str(item) for item in (data.get("tool_names") or []))
        if not NAME_PATTERN.fullmatch(name):
            continue
        if not set(tool_names) <= known:
            continue
        loaded[name] = SkillSpec(
            name=name,
            description=str(data.get("description") or ""),
            instructions=str(data.get("instructions") or ""),
            tool_names=tool_names,
            prompt_hint=str(data.get("prompt_hint") or ""),
            builtin=builtin,
        )
    return loaded


def _load_skills() -> dict[str, SkillSpec]:
    """扫描内置与自定义目录；内置技能优先，同名自定义配置被忽略。"""

    known = {spec.name for spec in tool_specs()}
    loaded = _scan_dir(SKILL_DIR, known, builtin=True)
    for name, spec in _scan_dir(USER_SKILL_DIR, known, builtin=False).items():
        if name not in loaded:
            loaded[name] = spec
    return loaded


_SKILLS = _load_skills()


def refresh_skills() -> None:
    """重新扫描技能目录；前端新增/删除技能后调用，使变更立即生效。"""

    global _SKILLS
    _SKILLS = _load_skills()


def list_skills() -> tuple[SkillSpec, ...]:
    return tuple(_SKILLS.values())


def get_skill(name: str) -> SkillSpec:
    try:
        return _SKILLS[name]
    except KeyError:
        raise KeyError(f"Unknown skill: {name}") from None


def tools_for_skill(skill: SkillSpec) -> list[BaseTool]:
    """按技能配置解析出实际 BaseTool 列表（引用 registry 单一事实来源）。"""

    by_name = {spec.name: spec.tool for spec in tool_specs()}
    return [by_name[name] for name in skill.tool_names]


def create_skill(
    name: str,
    instructions: str,
    description: str = "",
    prompt_hint: str = "",
    tool_names: tuple[str, ...] = (),
) -> SkillSpec:
    """新增自定义技能：校验后原子写入 data/skills/{name}.json 并立即生效。"""

    name = str(name or "").strip()
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError("name 必须匹配 [a-z0-9_-]+")
    if not str(instructions or "").strip():
        raise ValueError("instructions 不能为空")
    known = {spec.name for spec in tool_specs()}
    unknown = [item for item in tool_names if item not in known]
    if unknown:
        raise ValueError(f"未知工具：{unknown}")
    if name in {spec.name for spec in list_skills() if spec.builtin}:
        raise ValueError("不能覆盖内置技能")

    spec = SkillSpec(
        name=name,
        description=str(description or ""),
        instructions=str(instructions or ""),
        tool_names=tuple(tool_names),
        prompt_hint=str(prompt_hint or ""),
        builtin=False,
    )
    target = USER_SKILL_DIR / f"{name}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(spec), ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    refresh_skills()
    return spec


def delete_skill(name: str) -> bool:
    """删除自定义技能；内置技能受保护。"""

    spec = get_skill(name)
    if spec.builtin:
        raise ValueError("内置技能不可删除")
    target = USER_SKILL_DIR / f"{name}.json"
    if target.exists():
        target.unlink()
    refresh_skills()
    return True


@tool("load_skill")
def load_skill(skill_name: str, runtime: ToolRuntime[PersonaAgentContext]) -> Command:
    """Load an agent skill by name, exposing its tools to the current conversation."""

    skill = get_skill(skill_name)
    persona_id = runtime.context.persona_id
    visible_tools = [
        name for name in skill.tool_names if is_mcp_tool_visible(persona_id, name)
    ]
    hidden = [name for name in skill.tool_names if name not in visible_tools]
    loaded = list(runtime.state.get("loaded_skills") or [])
    instructions = skill.instructions
    if hidden:
        instructions += f"\n（未授权工具已隐藏：{', '.join(hidden)}）"
    # 工具直接改 runtime.state 不会保留到下一次模型调用；必须通过
    # Command(update=...) 走 LangGraph 状态更新协议，且回填对应 tool_call_id
    # 的 ToolMessage，保证工具调用协议闭合。
    update = [skill.name] if skill.name not in loaded else []
    return Command(
        update={
            "loaded_skills": update,
            "messages": [
                ToolMessage(
                    content=json.dumps(
                        {
                            "status": "loaded",
                            "skill": skill.name,
                            "instructions": instructions,
                            "tools": visible_tools,
                            "prompt_hint": skill.prompt_hint,
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=runtime.tool_call_id,
                )
            ],
        }
    )
