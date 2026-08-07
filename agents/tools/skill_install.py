"""对话式技能安装工具：GitHub/URL 拉取 → 校验 → 确认 → 落地。"""

import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from langchain.tools import ToolRuntime, tool

from agents.context import PersonaAgentContext
from agents.skill_sources import fetch_github_skill, fetch_url_skill
from agents.tools.management import request_confirmation

Confirmer = Callable[[dict], bool]
Fetcher = Callable[[str, str, str, str, str, Path], Path]


def _validate_package(skill_dir: Path, skills_module) -> tuple[dict | None, str]:
    from agents.registry import tool_specs
    from agents.skill_parser import parse_skill_dir

    parsed = parse_skill_dir(skill_dir)
    if parsed is None:
        return None, "SKILL.md 解析/校验失败"
    known = {spec.name for spec in tool_specs()}
    unknown = [t for t in parsed["tool_names"] if t not in known]
    if unknown:
        return None, f"未知工具: {', '.join(unknown)}"
    name = parsed["name"]
    if any(spec.name == name and spec.builtin for spec in skills_module.list_skills()):
        return None, f"与内置技能同名，不可覆盖: {name}"
    if (skills_module.USER_SKILL_DIR / name).exists():
        return None, f"技能已存在，请先删除: {name}"
    return parsed, ""


def install_skill_core(
    *,
    source_type: str,
    repo: str,
    path: str,
    ref: str,
    url: str,
    workdir: Path,
    confirmer: Confirmer,
    fetcher: Fetcher,
    skills_module,
) -> dict:
    """核心安装流程：拉取 → 校验 → 确认 → 落地。"""
    from agents.skill_sources import parse_github_url

    if source_type not in {"github", "url"}:
        return {"status": "error", "error": f"未知来源类型: {source_type}"}
    if url and ("github.com" in url or "raw.githubusercontent.com" in url):
        try:
            repo, ref, path = parse_github_url(url)
        except RuntimeError as exc:
            return {"status": "error", "error": str(exc)}
        source_type = "github"
    if source_type == "github" and ("/" not in repo or not path.strip()):
        return {"status": "error", "error": "GitHub 来源需要 repo(owner/repo) 与 path，或完整 GitHub 链接"}
    if source_type == "url" and not url.strip():
        return {"status": "error", "error": "URL 来源需要 url"}
    try:
        skill_dir = fetcher(source_type, repo, path, ref, url, workdir)
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}
    parsed, error = _validate_package(skill_dir, skills_module)
    if parsed is None:
        return {"status": "error", "error": error}
    name = parsed["name"]
    action = {
        "tool": "install_skill",
        "title": f"安装技能 {name}",
        "target": name,
        "arguments": {
            "source_type": source_type,
            "repo": repo,
            "path": path,
            "ref": ref,
            "url": url,
        },
    }
    if not confirmer(action):
        return {"status": "cancelled"}
    target = skills_module.USER_SKILL_DIR / name
    try:
        shutil.copytree(skill_dir, target)
    except OSError as exc:
        return {"status": "error", "error": f"写入失败: {exc}"}
    skills_module.refresh_skills()
    return {
        "status": "installed",
        "skill": name,
        "tools": list(parsed["tool_names"]),
        "scripts": list(parsed.get("scripts") or ()),
    }


@tool("install_skill")
def install_skill(
    source_type: str,
    repo: str,
    path: str,
    ref: str,
    url: str,
    runtime: ToolRuntime[PersonaAgentContext],
) -> dict:
    """Install a skill from GitHub (repo + path) or a direct URL; asks for confirmation."""
    from agents import skills as skills_module

    with tempfile.TemporaryDirectory() as tmp:
        workdir = Path(tmp)
        return install_skill_core(
            source_type=source_type,
            repo=repo or "",
            path=path or "",
            ref=ref or "main",
            url=url or "",
            workdir=workdir,
            confirmer=request_confirmation,
            fetcher=lambda st, r, p, rf, u, wd: (
                fetch_github_skill(r, p, rf, wd) if st == "github" else fetch_url_skill(u, wd)
            ),
            skills_module=skills_module,
        )


@tool("list_installable_skills")
def list_installable_skills(runtime: ToolRuntime[PersonaAgentContext]) -> dict:
    """List skills available for installation from the curated catalog."""
    from agents.skills import list_installable_skills as _catalog

    return _catalog()
