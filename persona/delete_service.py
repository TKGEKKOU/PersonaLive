"""Permanent deletion of one persona and every resource it owns."""

import logging
from collections.abc import Callable
from pathlib import Path
import shutil

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from agents.checkpoint import delete_persona_checkpoints
from app.models import (
    ConversationMessage,
    DocumentJob,
    KnowledgeSpace,
    Persona,
    PersonaDraft,
    PersonaMemory,
)
from ingestion.document_jobs import DATA_DIR
from ingestion.milvus_store import KnowledgeSpaceScope, MilvusRagStore
from persona.service import LOCAL_WORKSPACE_ID, PersonaNotFound
from settings import Settings


logger = logging.getLogger(__name__)

CheckpointCleaner = Callable[[str], None]


class PersonaDeletionService:
    """Coordinate deletion across Milvus, LangGraph, SQLite, and local files."""

    def __init__(
        self,
        settings: Settings | None = None,
        vector_store: MilvusRagStore | None = None,
        checkpoint_cleaner: CheckpointCleaner | None = None,
        data_dir: Path = DATA_DIR,
    ) -> None:
        self.settings = settings or Settings.load()
        self.vector_store = vector_store or MilvusRagStore(self.settings)
        self.checkpoint_cleaner = checkpoint_cleaner or (
            lambda persona_id: delete_persona_checkpoints(self.settings, persona_id)
        )
        self.data_dir = data_dir.resolve()

    def delete(self, session: Session, persona_id: str) -> None:
        persona = session.scalar(
            select(Persona).where(
                Persona.id == persona_id,
                Persona.workspace_id == LOCAL_WORKSPACE_ID,
            )
        )
        if persona is None:
            raise PersonaNotFound(persona_id)

        knowledge_space_id = persona.knowledge_space_id
        jobs = list(
            session.scalars(
                select(DocumentJob).where(
                    DocumentJob.workspace_id == LOCAL_WORKSPACE_ID,
                    DocumentJob.knowledge_space_id == knowledge_space_id,
                )
            )
        )
        messages = list(
            session.scalars(
                select(ConversationMessage).where(
                    ConversationMessage.persona_id == persona_id
                )
            )
        )
        file_directories = self._owned_directories(jobs)

        # 外部资源先删。Milvus 不可用时降级：记录告警但仍完成数据库删除，
        # 避免向量库故障导致角色永远无法删除（孤儿向量不可见且无引用，无害）。
        try:
            self.vector_store.delete_knowledge_space(
                KnowledgeSpaceScope(LOCAL_WORKSPACE_ID, knowledge_space_id)
            )
        except Exception as exc:  # noqa: BLE001 - 向量清理失败不应阻塞角色删除
            logger.warning("删除角色 %s 时 Milvus 向量清理失败：%s", persona_id, exc)
        self.checkpoint_cleaner(persona_id)

        try:
            session.execute(
                delete(ConversationMessage).where(
                    ConversationMessage.persona_id == persona_id
                )
            )
            session.execute(delete(PersonaMemory).where(PersonaMemory.persona_id == persona_id))
            session.execute(
                delete(PersonaDraft).where(
                    or_(
                        PersonaDraft.persona_id == persona_id,
                        PersonaDraft.knowledge_space_id == knowledge_space_id,
                    )
                )
            )
            session.execute(
                delete(DocumentJob).where(
                    DocumentJob.workspace_id == LOCAL_WORKSPACE_ID,
                    DocumentJob.knowledge_space_id == knowledge_space_id,
                )
            )
            session.delete(persona)
            session.flush()
            knowledge_space = session.get(KnowledgeSpace, knowledge_space_id)
            if knowledge_space is not None:
                session.delete(knowledge_space)
            session.commit()
        except Exception:
            session.rollback()
            raise

        for directory in file_directories:
            shutil.rmtree(directory, ignore_errors=True)
        self._delete_message_audio(messages)

    def _delete_message_audio(self, messages: list[ConversationMessage]) -> None:
        """删除聊天消息对应的本地音频文件（库记录已在事务内删除）。"""

        audio_root = (self.settings.project_root / "data" / "audio").resolve()
        for message in messages:
            if not message.audio_path:
                continue
            path = (audio_root / message.audio_path).resolve()
            if path.is_relative_to(audio_root):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _owned_directories(self, jobs: list[DocumentJob]) -> set[Path]:
        directories: set[Path] = set()
        for job in jobs:
            for raw_path in (job.source_path, job.markdown_path):
                if not raw_path:
                    continue
                path = Path(raw_path).resolve()
                if path.is_relative_to(self.data_dir):
                    directories.add(path if path.is_dir() else path.parent)
        return directories
