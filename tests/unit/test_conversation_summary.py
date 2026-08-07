"""会话级摘要服务：计数、增量 prompt、写入与并发防重。"""

from sqlalchemy import select

from app.conversation_summary import (
    build_summary_prompt,
    count_user_turns,
    get_conversation_summary,
    load_recent_turns,
    schedule_summary_after_turn,
)
from app.models import ConversationMessage, ConversationSummary


def _add_message(session, persona_id, conversation_id, role, content, kind="text"):
    session.add(
        ConversationMessage(
            workspace_id="w",
            persona_id=persona_id,
            conversation_id=conversation_id,
            role=role,
            kind=kind,
            content=content,
            status="completed",
        )
    )
    session.commit()


def test_count_user_turns_only_completed_text_or_audio(db_session):
    _add_message(db_session, "p1", "c1", "user", "你好")
    _add_message(db_session, "p1", "c1", "assistant", "嗨")
    _add_message(db_session, "p1", "c1", "user", "再说一遍", kind="audio")
    db_session.add(
        ConversationMessage(
            workspace_id="w",
            persona_id="p1",
            conversation_id="c1",
            role="user",
            kind="text",
            content="",
            status="completed",
        )
    )
    db_session.add(
        ConversationMessage(
            workspace_id="w",
            persona_id="p1",
            conversation_id="c1",
            role="user",
            kind="text",
            content="pending",
            status="pending",
        )
    )
    db_session.commit()
    assert count_user_turns(db_session, "w", "p1", "c1") == 2


def test_load_recent_turns_returns_window_text(db_session):
    for i in range(1, 23):
        _add_message(db_session, "p1", "c1", "user", f"u{i}")
        _add_message(db_session, "p1", "c1", "assistant", f"a{i}")
    text = load_recent_turns(db_session, "w", "p1", "c1", 10, 20)
    assert "u11" in text and "a11" in text
    assert "u20" in text and "a20" in text
    assert "u10" not in text
    assert "u21" not in text


def test_build_summary_prompt_contains_blocks():
    prompt = build_summary_prompt("旧摘要", "u1\na1")
    assert "旧摘要" in prompt
    assert "u1\na1" in prompt
    assert "500 字" in prompt


def test_schedule_and_get_summary_roundtrip(db_session, monkeypatch):
    from langchain_core.messages import HumanMessage, SystemMessage

    class FakeLLM:
        def invoke(self, messages):
            assert isinstance(messages[0], SystemMessage)
            assert "摘要器" in messages[0].content
            return type("R", (), {"content": "合并后的摘要"})()

    import app.conversation_summary as mod

    monkeypatch.setattr(mod, "get_llm", lambda: FakeLLM())
    for i in range(1, 11):
        _add_message(db_session, "p1", "c1", "user", f"u{i}")
        _add_message(db_session, "p1", "c1", "assistant", f"a{i}")
    mod._maybe_summarize(
        lambda: db_session, workspace_id="w", persona_id="p1", conversation_id="c1"
    )
    row = db_session.scalars(
        select(ConversationSummary).where(
            ConversationSummary.persona_id == "p1",
            ConversationSummary.conversation_id == "c1",
        )
    ).first()
    assert row is not None
    assert row.summary == "合并后的摘要"
    assert row.summarized_through_count == 10
    assert get_conversation_summary(db_session, "w", "p1", "c1") == "合并后的摘要"


def test_schedule_skips_when_count_not_multiple_of_10(db_session, monkeypatch):
    import app.conversation_summary as mod

    called = []
    monkeypatch.setattr(mod, "_generate_summary", lambda prompt: called.append(prompt) or "x")
    _add_message(db_session, "p1", "c2", "user", "u1")
    _add_message(db_session, "p1", "c2", "assistant", "a1")
    mod._maybe_summarize(
        lambda: db_session, workspace_id="w", persona_id="p1", conversation_id="c2"
    )
    assert called == []
    assert db_session.scalars(select(ConversationSummary)).first() is None


def test_schedule_skips_when_already_summarized(db_session, monkeypatch):
    import app.conversation_summary as mod

    called = []
    monkeypatch.setattr(
        mod, "_generate_summary", lambda prompt: called.append(prompt) or "x"
    )
    db_session.add(
        ConversationSummary(
            workspace_id="w",
            persona_id="p1",
            conversation_id="c3",
            summary="旧",
            summarized_through_count=10,
        )
    )
    db_session.commit()
    for i in range(1, 11):
        _add_message(db_session, "p1", "c3", "user", f"u{i}")
        _add_message(db_session, "p1", "c3", "assistant", f"a{i}")
    mod._maybe_summarize(
        lambda: db_session, workspace_id="w", persona_id="p1", conversation_id="c3"
    )
    assert called == []
    row = db_session.scalars(select(ConversationSummary)).one()
    assert row.summary == "旧"
    assert row.summarized_through_count == 10


def test_summary_failure_keeps_old_row(db_session, monkeypatch):
    import app.conversation_summary as mod

    def boom(prompt):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "_generate_summary", boom)
    for i in range(1, 11):
        _add_message(db_session, "p1", "c4", "user", f"u{i}")
        _add_message(db_session, "p1", "c4", "assistant", f"a{i}")
    mod._maybe_summarize(
        lambda: db_session, workspace_id="w", persona_id="p1", conversation_id="c4"
    )
    assert db_session.scalars(select(ConversationSummary)).first() is None


def test_schedule_starts_daemon_thread(db_session, monkeypatch):
    import app.conversation_summary as mod

    started = []

    class FakeThread:
        def __init__(self, target, args, kwargs, name, daemon):
            self.target = target
            started.append((daemon, target))

        def start(self):
            pass

    monkeypatch.setattr(mod.threading, "Thread", FakeThread)
    schedule_summary_after_turn(
        lambda: db_session, workspace_id="w", persona_id="p1", conversation_id="c5"
    )
    assert len(started) == 1
    assert started[0][0] is True
    assert started[0][1].__name__ == "_maybe_summarize"
