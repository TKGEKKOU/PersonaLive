"""SKILL.md 标准技能包解析(对齐 agentskills.io 规范)。"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")


def _is_unsafe_path(relative: str) -> bool:
    """技能包内相对路径安全校验：拒绝绝对路径、..、盘符。"""
    normalized = relative.replace("\\", "/")
    parts = normalized.split("/")
    return (
        normalized.startswith("/")
        or ":" in normalized
        or any(part in ("", ".", "..") for part in parts)
    )


def _scan_subdir(skill_dir: Path, sub: str, suffix: str | None = None) -> tuple[str, ...] | None:
    """扫描技能包子目录；存在不安全路径返回 None（整包非法）。"""
    base = skill_dir / sub
    if not base.is_dir():
        return ()
    collected: list[str] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(base).as_posix()
        if suffix and not rel.endswith(suffix):
            continue
        if _is_unsafe_path(rel):
            return None
        collected.append(rel)
    return tuple(collected)


def _split_frontmatter(text: str) -> tuple[str | None, str]:
    if not text.startswith("---"):
        return None, text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1 :])
    return None, text


def parse_skill_dir(skill_dir: Path) -> dict | None:
    """解析技能目录；非法（无 SKILL.md / frontmatter 违规）返回 None。"""

    skill_md = Path(skill_dir) / "SKILL.md"
    if not skill_md.is_file():
        return None
    frontmatter, body = _split_frontmatter(skill_md.read_text(encoding="utf-8"))
    if frontmatter is None:
        return None
    try:
        data = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    name = str(data.get("name") or "").strip()
    description = str(data.get("description") or "").strip()
    if not NAME_PATTERN.fullmatch(name) or name != Path(skill_dir).name:
        return None
    if not description or len(description) > 1024:
        return None
    tool_names_raw = data.get("tool-names") or []
    if not isinstance(tool_names_raw, list):
        return None
    tool_names = tuple(
        str(item).strip() for item in tool_names_raw if str(item).strip()
    )
    metadata: dict[str, str] = {}
    for key in ("license", "compatibility"):
        if data.get(key):
            metadata[key] = str(data[key])
    if isinstance(data.get("metadata"), dict):
        metadata.update({str(k): str(v) for k, v in data["metadata"].items()})
    prompt_hint = str(metadata.pop("prompt_hint", "") or "")
    scripts = _scan_subdir(skill_dir, "scripts", ".py")
    assets = _scan_subdir(skill_dir, "assets")
    references = _scan_subdir(skill_dir, "references")
    if scripts is None or assets is None or references is None:
        return None
    return {
        "name": name,
        "description": description,
        "instructions": body.strip(),
        "tool_names": tool_names,
        "metadata": metadata,
        "prompt_hint": prompt_hint,
        "scripts": scripts,
        "assets": assets,
        "references": references,
    }
