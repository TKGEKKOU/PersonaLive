"""SKILL.md 标准技能包解析测试。"""


def _write(tmp_path, name, body, dir_name=None):
    directory = tmp_path / (dir_name or name)
    directory.mkdir()
    (directory / "SKILL.md").write_text(body, encoding="utf-8")
    return directory


def test_parses_valid_skill(tmp_path):
    from agents.skill_parser import parse_skill_dir

    directory = _write(
        tmp_path,
        "pdf-tools",
        "---\nname: pdf-tools\ndescription: Extract PDFs. Use when handling PDF files.\n"
        "tool-names: [search_persona_knowledge]\n---\n# Do the thing\nStep by step.\n",
    )
    parsed = parse_skill_dir(directory)
    assert parsed["name"] == "pdf-tools"
    assert "Step by step" in parsed["instructions"]
    assert parsed["tool_names"] == ("search_persona_knowledge",)


def test_rejects_bad_name_or_description(tmp_path):
    from agents.skill_parser import parse_skill_dir

    assert (
        parse_skill_dir(
            _write(tmp_path, "BadName", "---\nname: BadName\ndescription: x\n---\nbody")
        )
        is None
    )
    assert (
        parse_skill_dir(
            _write(tmp_path, "a--b", "---\nname: a--b\ndescription: x\n---\nbody")
        )
        is None
    )
    assert (
        parse_skill_dir(
            _write(tmp_path, "ok-name", "---\nname: ok-name\ndescription: ''\n---\nbody")
        )
        is None
    )


def test_rejects_name_mismatch_and_missing_frontmatter(tmp_path):
    from agents.skill_parser import parse_skill_dir

    assert (
        parse_skill_dir(
            _write(tmp_path, "dir-a", "---\nname: other\ndescription: x\n---\nbody")
        )
        is None
    )
    assert parse_skill_dir(_write(tmp_path, "plain", "no frontmatter")) is None


def test_captures_metadata_and_rejects_bad_tool_names_type(tmp_path):
    from agents.skill_parser import parse_skill_dir

    parsed = parse_skill_dir(
        _write(
            tmp_path,
            "meta-skill",
            "---\nname: meta-skill\ndescription: x\nlicense: MIT\nmetadata:\n  author: me\n---\nbody",
        )
    )
    assert parsed["metadata"]["license"] == "MIT"
    assert parsed["metadata"]["author"] == "me"
    assert (
        parse_skill_dir(
            _write(
                tmp_path,
                "bad-tools",
                "---\nname: bad-tools\ndescription: x\ntool-names: not-a-list\n---\nbody",
            )
        )
        is None
    )


def _write_with_files(tmp_path, name, body, files):
    directory = tmp_path / name
    directory.mkdir()
    (directory / "SKILL.md").write_text(body, encoding="utf-8")
    for rel, content in files.items():
        target = directory / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return directory


def test_discovers_package_files(tmp_path):
    from agents.skill_parser import parse_skill_dir

    directory = _write_with_files(
        tmp_path,
        "pkg-skill",
        "---\nname: pkg-skill\ndescription: Package skill.\ntool-names: []\n---\nbody",
        {
            "scripts/a.py": "print('a')",
            "scripts/sub/b.py": "print('b')",
            "assets/data.txt": "x",
            "references/guide.md": "# guide",
        },
    )
    parsed = parse_skill_dir(directory)
    assert parsed["scripts"] == ("a.py", "sub/b.py")
    assert parsed["assets"] == ("data.txt",)
    assert parsed["references"] == ("guide.md",)
    assert parsed["prompt_hint"] == ""


def test_extracts_prompt_hint_from_metadata(tmp_path):
    from agents.skill_parser import parse_skill_dir

    parsed = parse_skill_dir(
        _write_with_files(
            tmp_path,
            "hint-skill",
            "---\nname: hint-skill\ndescription: x\nmetadata:\n  prompt_hint: 用户需要帮助时使用。\n---\nbody",
            {},
        )
    )
    assert parsed["prompt_hint"] == "用户需要帮助时使用。"


def test_rejects_unsafe_path_component():
    from agents.skill_parser import _is_unsafe_path

    assert _is_unsafe_path("a.py") is False
    assert _is_unsafe_path("sub/b.py") is False
    assert _is_unsafe_path("../a.py") is True
    assert _is_unsafe_path("a/../b.py") is True
    assert _is_unsafe_path("C:/x.py") is True
    assert _is_unsafe_path("/abs/x.py") is True
