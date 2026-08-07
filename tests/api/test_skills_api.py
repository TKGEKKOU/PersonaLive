def test_skills_api_list_create_delete(client, tmp_path, monkeypatch):
    import agents.skills as skills_module

    monkeypatch.setattr(skills_module, "USER_SKILL_DIR", tmp_path)

    listed = client.get("/api/skills")
    assert listed.status_code == 200
    names = {item["name"] for item in listed.json()}
    assert "document_management" in names

    created = client.post(
        "/api/skills",
        json={
            "name": "custom_skill",
            "description": "自定义技能",
            "instructions": "当用户要求执行自定义任务时使用。",
        },
    )
    assert created.status_code == 201
    assert created.json()["name"] == "custom_skill"
    assert created.json()["builtin"] is False

    deleted = client.delete("/api/skills/custom_skill")
    assert deleted.status_code == 204

    forbidden = client.delete("/api/skills/document_management")
    assert forbidden.status_code == 403


def test_skills_api_lists_available_tools(client):
    response = client.get("/api/skills/tools")

    assert response.status_code == 200
    names = {item["name"] for item in response.json()}
    assert "search_persona_knowledge" in names
    assert "add_persona_knowledge" in names


def _zip_bytes(entries):
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in entries:
            archive.writestr(path, content)
    buffer.seek(0)
    return buffer.read()


def test_upload_installs_valid_skill_package(client, tmp_path, monkeypatch):
    import agents.skills as skills_module

    monkeypatch.setattr(skills_module, "USER_SKILL_DIR", tmp_path)
    skills_module.refresh_skills()
    try:
        payload = _zip_bytes(
            [
                (
                    "pdf-tools/SKILL.md",
                    "---\nname: pdf-tools\ndescription: PDF tools. Use when handling PDFs.\n"
                    "tool-names: [search_persona_knowledge]\n---\nExtract text.\n",
                ),
                ("pdf-tools/references/REFERENCE.md", "details"),
            ]
        )
        response = client.post(
            "/api/skills/upload",
            files={"file": ("skills.zip", payload, "application/zip")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["installed"] == ["pdf-tools"]
        assert (tmp_path / "pdf-tools" / "SKILL.md").is_file()
        assert skills_module.get_skill("pdf-tools").format == "skillmd"
    finally:
        skills_module.refresh_skills()


def test_upload_rejects_path_traversal(client, tmp_path, monkeypatch):
    import agents.skills as skills_module

    monkeypatch.setattr(skills_module, "USER_SKILL_DIR", tmp_path)
    payload = _zip_bytes(
        [("../../evil/SKILL.md", "---\nname: evil\ndescription: x\n---\nbody")]
    )
    response = client.post(
        "/api/skills/upload",
        files={"file": ("evil.zip", payload, "application/zip")},
    )
    assert response.status_code == 400


def test_upload_skips_invalid_and_conflicts_with_builtin(client, tmp_path, monkeypatch):
    import agents.skills as skills_module

    monkeypatch.setattr(skills_module, "USER_SKILL_DIR", tmp_path)
    skills_module.refresh_skills()
    try:
        payload = _zip_bytes(
            [
                (
                    "bad-skill/SKILL.md",
                    "---\nname: Bad-Skill\ndescription: x\n---\nbody",
                ),
                (
                    "document_management/SKILL.md",
                    "---\nname: document_management\ndescription: x\n---\nbody",
                ),
            ]
        )
        response = client.post(
            "/api/skills/upload",
            files={"file": ("mixed.zip", payload, "application/zip")},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["installed"] == []
        reasons = {item["name"]: item["reason"] for item in body["skipped"]}
        assert "bad-skill" in reasons
        assert "document_management" in reasons
    finally:
        skills_module.refresh_skills()
