from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def new_uuid() -> str:
    return str(uuid4())


class KnowledgeSpace(Base):
    __tablename__ = "knowledge_spaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Persona(Base):
    __tablename__ = "personas"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    knowledge_space_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_spaces.id"), nullable=False, unique=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    persona_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="knowledge_expert"
    )
    profile_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class PersonaDraft(Base):
    __tablename__ = "persona_drafts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    knowledge_space_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_spaces.id"), nullable=False, unique=True
    )
    persona_id: Mapped[str | None] = mapped_column(
        ForeignKey("personas.id"), nullable=True, unique=True
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    persona_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="knowledge_expert"
    )
    candidates_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    selected_candidate_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    suggested_name: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class PersonaMemory(Base):
    __tablename__ = "persona_memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    persona_id: Mapped[str] = mapped_column(
        ForeignKey("personas.id"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    persona_id: Mapped[str] = mapped_column(ForeignKey("personas.id"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    audio_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    audio_content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DocumentJob(Base):
    __tablename__ = "document_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    knowledge_space_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_spaces.id"), nullable=False, index=True
    )
    document_id: Mapped[str] = mapped_column(String(36), nullable=False, default=new_uuid)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    markdown_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    source_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    markdown_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    markdown_preview: Mapped[str | None] = mapped_column(
        LONGTEXT().with_variant(Text(), "sqlite"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
