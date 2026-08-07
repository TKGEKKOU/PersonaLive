"""conversation_summaries 表结构与唯一约束。"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import Base
from app.models import ConversationSummary


def test_create_all_includes_conversation_summaries(db_session):
    assert "conversation_summaries" in Base.metadata.tables
    row = ConversationSummary(
        workspace_id="w",
        persona_id="p1",
        conversation_id="c1",
        summary="摘要",
        summarized_through_count=10,
    )
    db_session.add(row)
    db_session.commit()
    loaded = db_session.scalars(select(ConversationSummary)).one()
    assert loaded.summary == "摘要"
    assert loaded.summarized_through_count == 10


def test_unique_persona_conversation(db_session):
    db_session.add_all(
        [
            ConversationSummary(
                workspace_id="w",
                persona_id="p1",
                conversation_id="c1",
                summary="a",
                summarized_through_count=10,
            ),
            ConversationSummary(
                workspace_id="w",
                persona_id="p1",
                conversation_id="c1",
                summary="b",
                summarized_through_count=20,
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
