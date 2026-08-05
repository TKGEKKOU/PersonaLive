"""根据角色知识空间自动生成评测题集（免人工编写、免人工标注）。

设计口径（默认轻量档 5 题，另有标准 10 题 / 全面 15 题档位）：
- 可检索问题：由 LLM 从知识空间片段生成，自动填入 expected_chunk_ids
  （问题来源块的真实 chunk_id）——"真标注"，检索指标不受候选池限制；
- 复杂问题：要求结合多个片段推理/综合，更容易触发查询改写或生成纠错路径，
  用于检验自适应与自我纠正能力；
- 无关题探针：内置固定问题（PROBE_QUESTIONS），必然检索不到，用于测拒答；
  写入题集并标记 _probe，评测运行器识别后不会重复附加。

配比规则（可检索 : 复杂 : 探针）：5 题 = 2 : 2 : 1；10 题 = 5 : 3 : 2；
15 题 = 8 : 4 : 3；自定义 total 按同一公式推算。
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from ingestion.milvus_store import MilvusRagStore
from rag.contracts import RagQueryContext
from rag.eval.runner import PROBE_QUESTIONS
from rag.llm import get_llm
from rag.retriever import build_scope_expression


TIERS = {"fast": 5, "standard": 10, "thorough": 15}
DEFAULT_TIER = "fast"
DEFAULT_TOTAL = TIERS[DEFAULT_TIER]
BATCH_SIZE = 5
MAX_CHUNK_CHARS = 800
MIN_CHUNK_CHARS = 20
QUERY_LIMIT = 16384


QUESTION_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是知识库评测数据生成器。为每个片段生成 {per_chunk} 个不同角度的问题。"
            "要求：问题只能依靠对应片段回答；与片段内容强相关；模拟真实用户向角色提问的口吻；"
            "不要闲聊或泛泛而问；不要包含片段编号。"
            '只输出 JSON 数组，每个元素是 {{"index": 片段编号, "question": "问题"}}。',
        ),
        ("human", "片段列表：\n{chunks}"),
    ]
)

COMPLEX_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是知识库评测数据生成器。基于片段列表生成 {count} 个复杂问题："
            "每个问题必须结合多个片段的信息才能回答，可以是跨片段推理、对比或综合。"
            "要求：只能依靠这些片段回答；不要闲聊或泛泛而问；不要包含片段编号。"
            '只输出 JSON 数组，每个元素是 {{"question": "问题", "chunk_ids": [依赖的片段编号列表]}}。',
        ),
        ("human", "片段列表：\n{chunks}"),
    ]
)


def _invoke_llm(llm: Any, prompt: ChatPromptTemplate, values: dict) -> str:
    """用 ChatOpenAI（Runnable）直接调用并取文本，便于测试注入假模型。"""

    response = llm.invoke(prompt.format_messages(**values))
    content = getattr(response, "content", "")
    return content if isinstance(content, str) else str(content)


def _parse_json_array(text: str) -> list[Any]:
    """解析 JSON 数组，兼容代码围栏；非法输出抛 ValueError。"""

    normalized = (text or "").strip()
    if normalized.startswith("```"):
        lines = normalized.splitlines()
        normalized = "\n".join(lines[1:-1]).strip()
    value = json.loads(normalized)
    if not isinstance(value, list):
        raise ValueError("生成结果必须是 JSON 数组")
    return value


def load_chunks(
    workspace_id: str,
    knowledge_space_ids: list[str],
    max_chunks: int | None = None,
) -> list[tuple[str, str]]:
    """读取知识空间片段（chunk_id, text），按 chunk_id 稳定排序并过滤过短片段。"""

    store = MilvusRagStore()
    store.connect()
    expr = build_scope_expression(
        RagQueryContext(
            persona_id="question-generator",
            workspace_id=workspace_id,
            knowledge_space_ids=tuple(knowledge_space_ids),
        )
    )
    rows = store.client().query(
        collection_name=store.settings.collection_name,
        filter=expr,
        output_fields=["chunk_id", "text"],
        limit=QUERY_LIMIT,
    )
    chunks = [
        (str(row["chunk_id"]), str(row.get("text") or ""))
        for row in rows
        if row.get("chunk_id") and len(str(row.get("text") or "").strip()) >= MIN_CHUNK_CHARS
    ]
    chunks.sort(key=lambda item: item[0])
    if max_chunks:
        chunks = chunks[:max_chunks]
    return chunks


def _generate_content_rows(
    chunks: list[tuple[str, str]],
    llm: Any,
    per_chunk: int,
) -> list[dict[str, Any]]:
    """把片段分批生成问题；失败批次跳过，问题按全局顺序去重。"""

    rows: list[dict[str, Any]] = []
    for start in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[start : start + BATCH_SIZE]
        rendered = "\n\n".join(
            f"[{index}] {text[:MAX_CHUNK_CHARS]}" for index, (_, text) in enumerate(batch)
        )
        try:
            raw = _invoke_llm(
                llm,
                QUESTION_PROMPT,
                {"chunks": rendered, "per_chunk": max(1, per_chunk)},
            )
            items = _parse_json_array(raw)
        except Exception:
            continue
        by_index: dict[int, list[str]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            question = str(item.get("question") or "").strip()
            if isinstance(index, int) and 0 <= index < len(batch) and question:
                by_index.setdefault(index, []).append(question)
        for index, questions in by_index.items():
            chunk_id = batch[index][0]
            for question in questions[:per_chunk]:
                rows.append(
                    {
                        "question": question,
                        "expected_chunk_ids": [chunk_id],
                        "reference_answer": None,
                    }
                )
    seen: set[str] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in rows:
        if row["question"] in seen:
            continue
        seen.add(row["question"])
        unique_rows.append(row)
    return unique_rows


def _split_counts(total: int) -> tuple[int, int, int]:
    """按总数推算 (可检索, 复杂, 探针) 配比；总数过小时压缩复杂题保证可检索 ≥1。"""

    unretrievable = max(1, min(3, total // 5))
    complex_count = 2 if total <= 6 else min(4, total // 3)
    answerable = total - unretrievable - complex_count
    if answerable < 1:
        complex_count = max(1, total - unretrievable - 1)
        answerable = 1
    return answerable, complex_count, unretrievable


def _generate_complex_rows(
    chunks: list[tuple[str, str]],
    llm: Any,
    count: int,
) -> list[dict[str, Any]]:
    """生成需要结合多个片段的复杂问题；chunk_ids 映射为真实 chunk_id 标签。"""

    if not chunks or count <= 0:
        return []
    rows: list[dict[str, Any]] = []
    for start in range(0, len(chunks), BATCH_SIZE):
        if len(rows) >= count:
            break
        batch = chunks[start : start + BATCH_SIZE]
        rendered = "\n\n".join(
            f"[{index}] {text[:MAX_CHUNK_CHARS]}" for index, (_, text) in enumerate(batch)
        )
        try:
            raw = _invoke_llm(llm, COMPLEX_PROMPT, {"chunks": rendered, "count": count})
            items = _parse_json_array(raw)
        except Exception:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            raw_ids = item.get("chunk_ids")
            if not question or not isinstance(raw_ids, list):
                continue
            indices = [index for index in raw_ids if isinstance(index, int) and 0 <= index < len(batch)]
            if not indices:
                continue
            rows.append(
                {
                    "question": question,
                    "expected_chunk_ids": [batch[index][0] for index in indices],
                    "reference_answer": None,
                    "_complex": True,
                }
            )
            if len(rows) >= count:
                break
    return rows


def generate_question_set(
    chunks: list[tuple[str, str]],
    llm: Any,
    total: int | None = None,
    tier: str = DEFAULT_TIER,
    status: Any | None = None,
) -> list[dict[str, Any]]:
    """按档位/总数生成题集：可检索 + 复杂 + 无关探针，总数固定。"""

    active_total = TIERS.get(tier, DEFAULT_TOTAL) if total is None else total
    answerable_count, complex_count, probe_count = _split_counts(active_total)
    if not chunks:
        return [
            {"question": question, "expected_chunk_ids": [], "reference_answer": None, "_probe": True}
            for question in PROBE_QUESTIONS[:probe_count]
        ]
    # 可检索题只取一个批次（最多 5 块），片段不足配额时自动每块多出几题，
    # 保证生成阶段最多 2 次 LLM 调用且总数固定。
    source_chunks = chunks[:BATCH_SIZE]
    per_chunk = max(1, math.ceil(answerable_count / len(source_chunks)))
    if status is not None:
        status("生成可检索问题")
    content = _generate_content_rows(source_chunks, llm, per_chunk=per_chunk)[:answerable_count]
    if status is not None:
        status("生成复杂问题")
    content.extend(_generate_complex_rows(chunks, llm, complex_count)[:complex_count])
    content.extend(
        {
            "question": question,
            "expected_chunk_ids": [],
            "reference_answer": None,
            "_probe": True,
        }
        for question in PROBE_QUESTIONS[:probe_count]
    )
    return content


def _chunks_fingerprint(chunks: list[tuple[str, str]]) -> str:
    """对（chunk_id, text）排序序列做摘要，内容任何变化都会改变指纹。"""

    digest = hashlib.sha256()
    for chunk_id, text in chunks:
        digest.update(chunk_id.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(text.encode("utf-8"))
        digest.update(b"\x1f")
    return digest.hexdigest()


def generate_questions_for_persona(
    persona_id: str,
    workspace_id: str,
    knowledge_space_ids: list[str],
    out_path: Path | None = None,
    total: int | None = None,
    tier: str = DEFAULT_TIER,
    status: Any | None = None,
) -> Path:
    """读取角色知识空间、生成题集并写入 JSONL，返回输出路径。

    按知识内容指纹 + 档位缓存：资料未变化时直接复用上次生成的题集，
    不重复调用 LLM，避免反复浪费 token。
    """

    def say(text: str) -> None:
        if status is not None:
            status(text)

    say("读取角色知识空间")
    chunks = load_chunks(workspace_id, knowledge_space_ids)
    active_total = TIERS.get(tier, DEFAULT_TOTAL) if total is None else total
    output = out_path or (Path("data") / "eval" / f"questions_{persona_id}_{tier}_{active_total}.jsonl")
    meta_path = output.with_suffix(".meta.json")
    fingerprint = _chunks_fingerprint(chunks)
    if output.is_file() and meta_path.is_file():
        try:
            cached = json.loads(meta_path.read_text(encoding="utf-8"))
            if (
                cached.get("fingerprint") == fingerprint
                and cached.get("tier") == tier
                and cached.get("total") == active_total
            ):
                say("知识未变化，复用已生成的问题集")
                return output
        except (OSError, ValueError):
            pass

    rows = generate_question_set(chunks, get_llm(), total=active_total, tier=tier, status=status)
    say("写入问题集")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    meta_path.write_text(
        json.dumps(
            {
                "fingerprint": fingerprint,
                "tier": tier,
                "total": active_total,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return output
