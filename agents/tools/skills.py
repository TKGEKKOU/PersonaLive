"""技能脚本执行工具：只允许运行已加载技能自带 scripts/ 下的 Python 脚本。"""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from langchain.tools import ToolRuntime, tool

from agents.context import PersonaAgentContext
from agents.tools.management import request_confirmation

SCRIPT_TIMEOUT_SECONDS = 120
Runner = Callable[[Path, list[str], int], dict]
Confirmer = Callable[[dict], bool]


def _resolve_script(runtime_dir: Path, script: str) -> Path | None:
    """解析并校验脚本路径：必须落在 runtime_dir/scripts/ 内且为 .py。"""
    scripts_root = (runtime_dir / "scripts").resolve()
    candidate = (scripts_root / script).resolve()
    if candidate == scripts_root or scripts_root not in candidate.parents:
        return None
    if candidate.suffix != ".py" or not candidate.is_file():
        return None
    return candidate


def _run_python(script: Path, args: list[str], timeout: int = SCRIPT_TIMEOUT_SECONDS) -> dict:
    """用运行中解释器执行脚本，cwd 为技能运行目录。"""
    try:
        completed = subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(script.parent.parent),
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "timeout",
            "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
            "stderr": f"脚本执行超过 {timeout} 秒，已终止。",
            "exit_code": None,
        }
    return {
        "status": "ok" if completed.returncode == 0 else "failed",
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "exit_code": completed.returncode,
    }


def run_skill_script_core(
    *,
    skill_name: str,
    script: str,
    script_args: list[str],
    loaded_skills: tuple[str, ...] | list[str],
    confirmer: Confirmer,
    runner: Runner,
    runtime_dir: Path,
) -> dict:
    """核心执行流程：加载校验 → 路径校验 → 确认 → 执行。
    registry/落地校验由 @tool 包装层完成，便于单元测试注入假 confirmer/runner。"""
    if skill_name not in loaded_skills:
        return {"status": "error", "error": f"Skill not loaded: {skill_name}; call load_skill first"}
    target = _resolve_script(runtime_dir, script)
    if target is None:
        return {"status": "error", "error": f"Script not allowed: {script}"}
    action = {
        "tool": "run_skill_script",
        "title": f"执行技能脚本 {skill_name}/{script}",
        "target": f"{skill_name}/{script}",
        "arguments": {"script": script, "script_args": list(script_args)},
    }
    if not confirmer(action):
        return {"status": "cancelled"}
    return runner(target, list(script_args), SCRIPT_TIMEOUT_SECONDS)


@tool("run_skill_script")
def run_skill_script(
    skill_name: str,
    script: str,
    script_args: list[str],
    runtime: ToolRuntime[PersonaAgentContext],
) -> dict:
    """Run a bundled Python script from a loaded skill; asks the user for confirmation."""
    # 惰性导入：agents.skills -> agents.registry -> agents.tools 存在环，避免模块级导入。
    from agents.skills import RUNTIME_SKILL_DIR, ensure_landed, get_skill

    loaded = tuple(runtime.state.get("loaded_skills") or [])
    try:
        skill = get_skill(skill_name)
    except KeyError:
        return {"status": "error", "error": f"Unknown skill: {skill_name}"}
    if not skill.enabled:
        return {"status": "error", "error": f"Skill disabled: {skill_name}"}
    if not skill.scripts:
        return {"status": "error", "error": f"Skill has no scripts: {skill_name}"}
    if not ensure_landed(skill):
        return {"status": "error", "error": "Skill scripts are not ready (landing failed)"}
    return run_skill_script_core(
        skill_name=skill_name,
        script=script,
        script_args=script_args,
        loaded_skills=loaded,
        confirmer=request_confirmation,
        runner=_run_python,
        runtime_dir=RUNTIME_SKILL_DIR / skill_name,
    )
