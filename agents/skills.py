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
import shutil
from dataclasses import asdict, dataclass, field
from dataclasses import replace
from pathlib import Path

import yaml
from langchain.messages import ToolMessage
from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from langgraph.types import Command

from agents.context import PersonaAgentContext
from agents.mcp_grants import is_mcp_tool_visible
from agents.registry import tool_specs
from agents.skill_parser import parse_skill_dir
from settings import Settings


SKILL_DIR = Path(__file__).resolve().parent / "skills"
SKILL_CATALOG_PATH = Path(__file__).resolve().parent / "skill_catalog.json"
USER_SKILL_DIR = Settings.load().project_root / "data" / "skills"
RUNTIME_SKILL_DIR = Settings.load().project_root / "data" / "skills" / "runtime"
SKILL_STATE_PATH = USER_SKILL_DIR / "skills_state.json"
NAME_PATTERN = re.compile(r"[a-z0-9_-]+")


def _skillmd_text(
    name: str,
    description: str,
    tool_names: tuple[str, ...],
    instructions: str,
    prompt_hint: str = "",
) -> str:
    metadata: dict = {}
    if prompt_hint:
        metadata["prompt_hint"] = prompt_hint
    frontmatter: dict = {
        "name": name,
        "description": description,
        "tool-names": list(tool_names),
    }
    if metadata:
        frontmatter["metadata"] = metadata
    body = "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + "---\n\n"
    return body + str(instructions or "").strip() + "\n"


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
    format: str = "json"
    enabled: bool = True
    metadata: dict = field(default_factory=dict)
    scripts: tuple[str, ...] = ()
    assets: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    source_dir: str = ""


def _load_skill_states() -> dict[str, bool]:
    """读取技能启停状态；缺省视为启用。"""

    if not SKILL_STATE_PATH.is_file():
        return {}
    try:
        raw = json.loads(SKILL_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    states: dict[str, bool] = {}
    if isinstance(raw, dict):
        for name, value in raw.items():
            if isinstance(value, bool):
                states[name] = value
            elif isinstance(value, dict) and isinstance(value.get("enabled"), bool):
                states[name] = value["enabled"]
    return states


def _save_skill_state(name: str, enabled: bool) -> None:
    states = _load_skill_states()
    states[name] = enabled
    SKILL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SKILL_STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(states, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SKILL_STATE_PATH)


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
            format="json",
        )
    for path in sorted(directory.glob("*/SKILL.md")):
        if path.parent.name == "runtime":
            continue
        parsed = parse_skill_dir(path.parent)
        if parsed is None or parsed["name"] in loaded:
            continue
        if not set(parsed["tool_names"]) <= known:
            continue
        loaded[parsed["name"]] = SkillSpec(
            name=parsed["name"],
            description=parsed["description"],
            instructions=parsed["instructions"],
            tool_names=parsed["tool_names"],
            prompt_hint=parsed.get("prompt_hint", ""),
            builtin=builtin,
            format="skillmd",
            metadata=parsed["metadata"],
            scripts=parsed.get("scripts", ()),
            assets=parsed.get("assets", ()),
            references=parsed.get("references", ()),
            source_dir=str(path.parent),
        )
    return loaded


def _load_skills() -> dict[str, SkillSpec]:
    """扫描内置与自定义目录；内置技能优先，同名自定义配置被忽略。"""

    known = {spec.name for spec in tool_specs()}
    states = _load_skill_states()
    loaded = _scan_dir(SKILL_DIR, known, builtin=True)
    for name, spec in _scan_dir(USER_SKILL_DIR, known, builtin=False).items():
        if name not in loaded:
            loaded[name] = spec
    for name, spec in loaded.items():
        enabled = states.get(name, True)
        if not enabled:
            loaded[name] = replace(spec, enabled=False)
    return loaded


_SKILLS = _load_skills()


def refresh_skills() -> None:
    """重新扫描技能目录；前端新增/删除技能后调用，使变更立即生效。"""

    global _SKILLS
    _SKILLS = _load_skills()


def land_skill(skill: SkillSpec) -> Path:
    """把技能包内 scripts/assets/references 幂等复制到运行目录。"""

    target = RUNTIME_SKILL_DIR / skill.name
    if not skill.source_dir:
        return target
    source = Path(skill.source_dir)
    if not source.is_dir():
        return target
    for sub in ("scripts", "assets", "references"):
        src = source / sub
        if not src.is_dir():
            continue
        dst = target / sub
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
    return target


def ensure_landed(skill: SkillSpec) -> bool:
    """确保技能包已落地；含脚本的技能要求 scripts/ 已就绪。"""

    try:
        target = land_skill(skill)
    except OSError:
        return False
    if skill.scripts and not (target / "scripts").is_dir():
        return False
    return True


def list_installable_skills() -> dict:
    """读取 curated 技能目录，标注已安装/内置同名冲突。"""
    try:
        data = json.loads(SKILL_CATALOG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    installed = {spec.name for spec in list_skills()}
    builtin_names = {spec.name for spec in list_skills() if spec.builtin}
    items = []
    for entry in data.get("skills") or []:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        items.append(
            {
                "name": name,
                "description": str(entry.get("description") or ""),
                "repo": str(entry.get("repo") or ""),
                "path": str(entry.get("path") or ""),
                "ref": str(entry.get("ref") or "main"),
                "installed": name in installed,
                "conflict": name in builtin_names,
            }
        )
    return {"items": items}


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
    target = USER_SKILL_DIR / name / "SKILL.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(
        _skillmd_text(
            name,
            str(description or ""),
            tuple(tool_names),
            str(instructions or ""),
            str(prompt_hint or ""),
        ),
        encoding="utf-8",
    )
    os.replace(tmp, target)
    refresh_skills()
    return get_skill(name)


def delete_skill(name: str) -> bool:
    """删除自定义技能（JSON 文件或标准技能包目录）；内置技能受保护。"""

    spec = get_skill(name)
    if spec.builtin:
        raise ValueError("内置技能不可删除")
    json_target = USER_SKILL_DIR / f"{name}.json"
    dir_target = USER_SKILL_DIR / name
    if json_target.exists():
        json_target.unlink()
    elif dir_target.is_dir():
        shutil.rmtree(dir_target)
    else:
        raise KeyError(name)
    shutil.rmtree(RUNTIME_SKILL_DIR / name, ignore_errors=True)
    states = _load_skill_states()
    if name in states:
        states.pop(name)
        SKILL_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SKILL_STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(states, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, SKILL_STATE_PATH)
    refresh_skills()
    return True


def set_skill_enabled(name: str, enabled: bool) -> SkillSpec:
    """切换技能启用状态（内置与自定义均可停用）。"""

    get_skill(name)
    _save_skill_state(name, bool(enabled))
    refresh_skills()
    return get_skill(name)


def update_skill(
    name: str,
    *,
    description: str | None = None,
    instructions: str | None = None,
    prompt_hint: str | None = None,
    tool_names: list[str] | None = None,
) -> SkillSpec:
    """编辑自定义 SKILL.md 技能包；内置技能与旧版 JSON 技能只读。"""

    spec = get_skill(name)
    if spec.builtin:
        raise ValueError("内置技能不可编辑")
    if spec.format == "json":
        raise ValueError("旧版 JSON 技能只读，请删除后重建 SKILL.md 技能包")
    target = USER_SKILL_DIR / name / "SKILL.md"
    if not target.is_file():
        raise KeyError(name)
    new_description = str(description) if description is not None else spec.description
    new_instructions = str(instructions) if instructions is not None else spec.instructions
    new_prompt_hint = str(prompt_hint) if prompt_hint is not None else spec.prompt_hint
    new_tools = tuple(str(item) for item in tool_names) if tool_names is not None else spec.tool_names
    known = {s.name for s in tool_specs()}
    unknown = [item for item in new_tools if item not in known]
    if unknown:
        raise ValueError(f"未知工具：{unknown}")
    tmp = target.with_suffix(".md.tmp")
    tmp.write_text(
        _skillmd_text(name, new_description, new_tools, new_instructions, new_prompt_hint),
        encoding="utf-8",
    )
    os.replace(tmp, target)
    refresh_skills()
    return get_skill(name)


@tool("load_skill")
def load_skill(skill_name: str, runtime: ToolRuntime[PersonaAgentContext]) -> Command:
    """Load an agent skill by name, exposing its tools to the current conversation."""

    skill = get_skill(skill_name)
    if not skill.enabled:
        return Command(
            update={
                "messages": [
                    ToolMessage(
                        content=json.dumps(
                            {"status": "disabled", "skill": skill_name, "instructions": ""},
                            ensure_ascii=False,
                        ),
                        tool_call_id=runtime.tool_call_id,
                    )
                ]
            }
        )
    persona_id = runtime.context.persona_id
    visible_tools = [
        name for name in skill.tool_names if is_mcp_tool_visible(persona_id, name)
    ]
    hidden = [name for name in skill.tool_names if name not in visible_tools]
    loaded = list(runtime.state.get("loaded_skills") or [])
    instructions = skill.instructions
    if skill.scripts and not ensure_landed(skill):
        instructions += "\n（技能脚本未就绪，run_skill_script 暂不可用）"
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
