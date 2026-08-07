"""技能源拉取层测试：直连下载、安全解压与目录定位。"""

import io
import zipfile
from pathlib import Path

import pytest


def _zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in entries:
            archive.writestr(path, content)
    buffer.seek(0)
    return buffer.getvalue()


def test_extract_locates_skill_dir(tmp_path):
    from agents.skill_sources import extract_skill_archive

    archive = tmp_path / "pkg.zip"
    archive.write_bytes(
        _zip_bytes(
            [
                ("pdf-tools/SKILL.md", "---\nname: pdf-tools\ndescription: x\n---\nbody"),
                ("pdf-tools/scripts/run.py", "print('ok')\n"),
            ]
        )
    )
    skill_dir = extract_skill_archive(archive, tmp_path / "out")
    assert skill_dir.name == "pdf-tools"
    assert (skill_dir / "SKILL.md").is_file()


def test_extract_rejects_traversal_and_symlink(tmp_path):
    from agents.skill_sources import extract_skill_archive

    bad = tmp_path / "bad.zip"
    bad.write_bytes(
        _zip_bytes(
            [
                ("../evil/SKILL.md", "---\nname: evil\ndescription: x\n---\nbody"),
            ]
        )
    )
    try:
        extract_skill_archive(bad, tmp_path / "out1")
    except RuntimeError as exc:
        assert "路径" in str(exc)
    else:
        raise AssertionError("应拒绝路径穿越")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120000 << 16) | 0o777
        archive.writestr(info, "target")
    sym = tmp_path / "sym.zip"
    sym.write_bytes(buffer.getvalue())
    try:
        extract_skill_archive(sym, tmp_path / "out2")
    except RuntimeError as exc:
        assert "符号链接" in str(exc)
    else:
        raise AssertionError("应拒绝符号链接")


def test_fetch_github_skill_downloads_and_locates(tmp_path, monkeypatch):
    from agents.skill_sources import fetch_github_skill

    calls = {}

    class FakeResponse:
        def __init__(self):
            self.data = _zip_bytes(
                [
                    ("openai-skills-main/skills/pdf-tools/SKILL.md", "---\nname: pdf-tools\ndescription: x\n---\nbody"),
                ]
            )
            self.pos = 0

        def read(self, size=-1):
            if size < 0:
                chunk = self.data[self.pos :]
                self.pos = len(self.data)
                return chunk
            chunk = self.data[self.pos : self.pos + size]
            self.pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_opener(url, timeout):
        calls["url"] = url
        return FakeResponse()

    monkeypatch.setattr("agents.skill_sources._open_url", fake_opener)
    skill_dir = fetch_github_skill("openai/skills", "skills/pdf-tools", "main", tmp_path)
    assert "archive/refs/heads/main.zip" in calls["url"]
    assert skill_dir.name == "pdf-tools"
    assert (skill_dir / "SKILL.md").is_file()


def test_parse_github_url_variants():
    from agents.skill_sources import parse_github_url

    assert parse_github_url("https://github.com/openai/skills") == ("openai/skills", "main", "")
    assert parse_github_url("https://github.com/openai/skills/tree/main/skills/pdf-tools") == (
        "openai/skills",
        "main",
        "skills/pdf-tools",
    )
    assert parse_github_url("https://github.com/openai/skills/blob/main/skills/pdf-tools/SKILL.md") == (
        "openai/skills",
        "main",
        "skills/pdf-tools/SKILL.md",
    )
    assert parse_github_url("https://raw.githubusercontent.com/openai/skills/main/skills/pdf-tools") == (
        "openai/skills",
        "main",
        "skills/pdf-tools",
    )
    try:
        parse_github_url("https://example.com/x")
    except RuntimeError:
        pass
    else:
        raise AssertionError("应拒绝非 GitHub 链接")


def test_download_rejects_archive_over_limit(monkeypatch, tmp_path):
    import io

    from agents import skill_sources

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(skill_sources, "MAX_ZIP_BYTES", 4)
    monkeypatch.setattr(skill_sources, "_open_url", lambda *args: Response(b"12345"))
    destination = tmp_path / "skill.zip"

    with pytest.raises(RuntimeError, match="25MB"):
        skill_sources._download("https://example.com/skill.zip", destination)
    assert not destination.exists()
