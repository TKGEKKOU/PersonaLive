"""Parse Markdown files into scope-bound LangChain documents."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


@dataclass(frozen=True)
class DocumentScope:
    workspace_id: str
    knowledge_space_id: str
    document_id: str


class MarkdownParser:
    """结构感知分块器：把 Markdown 文档切成适合检索的语义块。

    设计要点：
    - 分隔符按优先级排列（标题 > 段落 > 句子 > 词），优先保留章节/标题层级边界，
      避免固定 token 切分打断语义；
    - 每个块写入来源路径、标题、section、doc_id、chunk_id 等元数据，供检索阶段
      做作用域过滤与引用溯源；
    - 内容哈希用于增量入库去重，重复上传不会产生重复向量。
    """

    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 150):
        safe_overlap = min(chunk_overlap, max(0, chunk_size // 4))
        self.splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=safe_overlap,
            separators=["\n## ", "\n### ", "\n\n", "\n", "\u3002", ".", " ", ""],
        )

    def parse_file(self, path: Path, scope: DocumentScope) -> list[Document]:
        text = path.read_text(encoding="utf-8")
        source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        title = self.extract_title(text, path.stem)
        base_metadata = {
            "source": str(path),
            "filename": path.name,
            "filetype": "text/markdown",
            "title": title,
            "section": title,
            "category": "content",
            "doc_id": path.stem,
        }

        documents: list[Document] = []
        for index, chunk in enumerate(self.splitter.split_text(text)):
            metadata = dict(base_metadata)
            metadata["chunk_id"] = f"{path.stem}:{index:04d}"
            metadata.update(
                {
                    "workspace_id": scope.workspace_id,
                    "knowledge_space_id": scope.knowledge_space_id,
                    "document_id": scope.document_id,
                    "source_hash": source_hash,
                }
            )
            documents.append(Document(page_content=chunk, metadata=metadata))
        return documents

    @staticmethod
    def extract_title(text: str, default: str) -> str:
        match = re.search(r"(?m)^#\s+(.+?)\s*$", text)
        if match:
            return match.group(1).strip()[:1000]
        return default[:1000]
