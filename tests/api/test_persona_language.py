from pathlib import Path

import pytest

from agents.context import PersonaAgentContext
from agents.workflow import _supervisor_prompt


def build_context(language: str = "") -> PersonaAgentContext:
    profile = {}
    if language:
        profile["reply_language"] = language
    return PersonaAgentContext(
        persona_id="p1",
        workspace_id="w1",
        knowledge_space_ids=("k1",),
        conversation_id="c1",
        persona_name="月华",
        persona_type="knowledge_expert",
        persona_profile=profile,
    )


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("zh", "Always reply in Chinese"),
        ("en", "Always reply in English"),
        ("ja", "Always reply in Japanese"),
        ("EN", "Always reply in English"),
    ],
)
def test_reply_language_injected_into_supervisor_prompt(language, expected):
    prompt = _supervisor_prompt(build_context(language))
    assert expected in prompt
    assert "regardless of the language the user writes in" in prompt


def test_no_reply_language_leaves_prompt_unchanged():
    prompt = _supervisor_prompt(build_context())
    assert "Always reply in" not in prompt


def test_persona_patch_saves_reply_language(client):
    persona = client.post("/api/personas", json={"name": "Language", "profile": {}}).json()
    updated = client.patch(
        f"/api/personas/{persona['id']}",
        json={"profile": {"reply_language": "ja"}},
    )
    assert updated.status_code == 200
    assert updated.json()["profile"]["reply_language"] == "ja"


def test_persona_edit_page_has_language_selector():
    html = (Path(__file__).resolve().parents[2] / "static" / "views" / "manage.html").read_text(encoding="utf-8")
    script = (Path(__file__).resolve().parents[2] / "static" / "js" / "personas.js").read_text(encoding="utf-8")
    assert 'id="edit-persona-language"' in html
    assert 'value="zh"' in html
    assert 'value="en"' in html
    assert 'value="ja"' in html
    assert "reply_language" in script
