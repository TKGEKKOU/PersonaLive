"""install_skill 工具测试：校验、确认与落地。"""

from pathlib import Path


def _make_package(tmp_path: Path, name: str = "demo-skill") -> Path:
    skill_dir = tmp_path / name
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: demo.\ntool-names: []\n---\nbody\n",
        encoding="utf-8",
    )
    return skill_dir


def test_install_skill_core_validates_and_installs(tmp_path, monkeypatch):
    import agents.skills as skills_module
    from agents.tools.skill_install import install_skill_core

    target = tmp_path / "skills"
    monkeypatch.setattr(skills_module, "USER_SKILL_DIR", target)
    package = _make_package(tmp_path)
    seen = {}

    def fake_fetcher(source_type, repo, path, ref, url, workdir):
        return package

    def fake_confirmer(action):
        seen["action"] = action
        return True

    result = install_skill_core(
        source_type="github",
        repo="demo/repo",
        path="skills/demo-skill",
        ref="main",
        url="",
        workdir=tmp_path / "work",
        confirmer=fake_confirmer,
        fetcher=fake_fetcher,
        skills_module=skills_module,
    )
    assert result["status"] == "installed"
    assert (target / "demo-skill" / "SKILL.md").is_file()
    assert seen["action"]["arguments"]["repo"] == "demo/repo"
    skills_module.refresh_skills()
    assert skills_module.get_skill("demo-skill").format == "skillmd"


def test_install_skill_core_rejects_conflict_and_unknown_tools(tmp_path, monkeypatch):
    import agents.skills as skills_module
    from agents.tools.skill_install import install_skill_core

    target = tmp_path / "skills"
    monkeypatch.setattr(skills_module, "USER_SKILL_DIR", target)
    package = tmp_path / "bad-skill"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\nname: bad-skill\ndescription: x\ntool-names: [no_such_tool]\n---\nbody\n",
        encoding="utf-8",
    )
    result = install_skill_core(
        source_type="github",
        repo="demo/repo",
        path="bad-skill",
        ref="main",
        url="",
        workdir=tmp_path / "work2",
        confirmer=lambda action: True,
        fetcher=lambda *a, **k: package,
        skills_module=skills_module,
    )
    assert result["status"] == "error"
    assert "未知工具" in result["error"]

    builtin_dir = tmp_path / "document_management"
    builtin_dir.mkdir()
    (builtin_dir / "SKILL.md").write_text(
        "---\nname: document_management\ndescription: x\ntool-names: []\n---\nbody\n",
        encoding="utf-8",
    )
    result = install_skill_core(
        source_type="github",
        repo="demo/repo",
        path="document_management",
        ref="main",
        url="",
        workdir=tmp_path / "work3",
        confirmer=lambda action: True,
        fetcher=lambda *a, **k: builtin_dir,
        skills_module=skills_module,
    )
    assert result["status"] == "error"
    assert "内置" in result["error"]


def test_install_skill_core_routes_github_link(tmp_path, monkeypatch):
    import agents.skills as skills_module
    from agents.tools.skill_install import install_skill_core

    target = tmp_path / "skills"
    monkeypatch.setattr(skills_module, "USER_SKILL_DIR", target)
    package = tmp_path / "pdf-tools"
    package.mkdir()
    (package / "SKILL.md").write_text(
        "---\nname: pdf-tools\ndescription: x.\ntool-names: []\n---\nbody\n",
        encoding="utf-8",
    )
    seen = {}

    def fake_fetcher(source_type, repo, path, ref, url, workdir):
        seen.update(source_type=source_type, repo=repo, path=path, ref=ref)
        return package

    result = install_skill_core(
        source_type="url",
        repo="",
        path="",
        ref="main",
        url="https://github.com/openai/skills/tree/main/skills/pdf-tools",
        workdir=tmp_path / "work",
        confirmer=lambda action: True,
        fetcher=fake_fetcher,
        skills_module=skills_module,
    )
    assert result["status"] == "installed"
    assert seen["source_type"] == "github"
    assert seen["repo"] == "openai/skills"
    assert seen["path"] == "skills/pdf-tools"
