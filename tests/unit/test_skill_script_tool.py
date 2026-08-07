"""run_skill_script 工具测试：路径安全、确认、执行与超时。"""

from pathlib import Path


def _skill_dir(tmp_path: Path, name: str = "demo") -> Path:
    runtime_dir = tmp_path / "runtime" / name
    (runtime_dir / "scripts").mkdir(parents=True)
    (runtime_dir / "scripts" / "hello.py").write_text(
        "import sys\nprint('hello', *sys.argv[1:])\n",
        encoding="utf-8",
    )
    return runtime_dir


def _runner_ok(script, args, timeout):
    return {"status": "ok", "stdout": "", "stderr": "", "exit_code": 0}


def test_core_rejects_unloaded_or_missing_script(tmp_path):
    from agents.tools.skills import run_skill_script_core

    runtime_dir = _skill_dir(tmp_path)
    result = run_skill_script_core(
        skill_name="demo",
        script="hello.py",
        script_args=["x"],
        loaded_skills=(),
        confirmer=lambda action: True,
        runner=_runner_ok,
        runtime_dir=runtime_dir,
    )
    assert result["status"] == "error"
    assert "not loaded" in result["error"]

    result = run_skill_script_core(
        skill_name="demo",
        script="../evil.py",
        script_args=[],
        loaded_skills=("demo",),
        confirmer=lambda action: True,
        runner=_runner_ok,
        runtime_dir=runtime_dir,
    )
    assert result["status"] == "error"


def test_core_confirms_then_runs(tmp_path):
    from agents.tools.skills import run_skill_script_core

    runtime_dir = _skill_dir(tmp_path)
    seen = {}

    def fake_confirmer(action):
        seen["action"] = action
        return True

    def fake_runner(script, args, timeout):
        seen["script"] = script
        seen["args"] = args
        return {"status": "ok", "stdout": "hello x", "stderr": "", "exit_code": 0}

    result = run_skill_script_core(
        skill_name="demo",
        script="hello.py",
        script_args=["x"],
        loaded_skills=("demo",),
        confirmer=fake_confirmer,
        runner=fake_runner,
        runtime_dir=runtime_dir,
    )
    assert result["status"] == "ok"
    assert seen["action"]["tool"] == "run_skill_script"
    assert seen["script"].name == "hello.py"
    assert seen["args"] == ["x"]


def test_core_cancels_when_denied(tmp_path):
    from agents.tools.skills import run_skill_script_core

    runtime_dir = _skill_dir(tmp_path)
    result = run_skill_script_core(
        skill_name="demo",
        script="hello.py",
        script_args=[],
        loaded_skills=("demo",),
        confirmer=lambda action: False,
        runner=_runner_ok,
        runtime_dir=runtime_dir,
    )
    assert result["status"] == "cancelled"


def test_runner_reports_timeout_and_nonzero(tmp_path):
    from agents.tools.skills import _run_python

    script = _skill_dir(tmp_path) / "scripts" / "hello.py"
    assert _run_python(script, ["a"], timeout=5)["exit_code"] == 0
    slow = script.parent / "slow.py"
    slow.write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    result = _run_python(slow, [], timeout=1)
    assert result["status"] == "timeout"
