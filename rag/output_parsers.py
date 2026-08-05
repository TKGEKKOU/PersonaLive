import json
from typing import Literal

from pydantic import BaseModel, Field


class RouteDecision(BaseModel):
    datasource: Literal["vectorstore", "web_search"]


class BinaryScore(BaseModel):
    binary_score: Literal["yes", "no"]


class BatchDocumentScore(BaseModel):
    # relevant_ids 引用批量 Prompt 中的片段编号；解析后还会做一次越界过滤。
    relevant_ids: list[int] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class AnswerQualityScore(BaseModel):
    grounded: bool = False
    useful: bool = False
    missing_points: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    correction_action: Literal["regenerate", "retrieve_again", "web_search", "no_answer"] = "regenerate"


def _parse_json_object(text: str) -> dict:
    """兼容模型常见的 JSON 代码围栏，但拒绝说明文字和非 object 结果。"""

    normalized = (text or "").strip()
    if normalized.startswith("```") and normalized.endswith("```"):
        lines = normalized.splitlines()
        normalized = "\n".join(lines[1:-1]).strip()
    value = json.loads(normalized)
    if not isinstance(value, dict):
        raise ValueError("评分结果必须是 JSON object")
    return value


def parse_batch_document_score(
    text: str,
    document_count: int,
    strict: bool = False,
) -> BatchDocumentScore:
    """解析一次批量文档评分。

    默认（strict=False）解析失败时保留全部候选片段但降为 0 置信度，避免误删
    证据并强制进入质量门；评测等需要"失败即无相关"的场景传 strict=True，
    解析失败时按没有任何相关片段处理，防止把失败误判为全部相关。
    """

    if strict:
        fallback = BatchDocumentScore()
    else:
        fallback = BatchDocumentScore(relevant_ids=list(range(max(0, document_count))), confidence=0.0)
    try:
        score = BatchDocumentScore.model_validate(_parse_json_object(text))
    except (ValueError, TypeError):
        return fallback

    relevant_ids = []
    for document_id in score.relevant_ids:
        if 0 <= document_id < document_count and document_id not in relevant_ids:
            relevant_ids.append(document_id)
    return score.model_copy(update={"relevant_ids": relevant_ids})


def parse_answer_quality_score(text: str) -> AnswerQualityScore:
    """解析综合回答评分；任何含糊输出都按未通过处理。"""

    # 质量评分失败必须 fail closed：不能把无法解析的结果当作检查通过。
    try:
        return AnswerQualityScore.model_validate(_parse_json_object(text))
    except (ValueError, TypeError):
        return AnswerQualityScore()


# 领域关键词为模型路由提供保守兜底，复制项目后应按业务调整。
LOCAL_KNOWLEDGE_KEYWORDS = (
    "milvus",
    "collection",
    "vector",
    "embedding",
    "向量",
    "知识库",
    "资料",
    "文档",
    "检索",
    "索引",
)


def parse_route_response(text: str, question: str) -> RouteDecision:
    """把 LLM 的路由输出解析成固定枚举。

    这里故意不用结构化输出接口，因为不同 OpenAI-compatible 服务对 function calling /
    JSON schema 的兼容性不一致。普通文本 + 本地解析更稳。
    """

    normalized = (text or "").strip().lower()
    question_normalized = (question or "").strip().lower()

    if any(keyword in question_normalized for keyword in LOCAL_KNOWLEDGE_KEYWORDS):
        return RouteDecision(datasource="vectorstore")
    if "vectorstore" in normalized:
        return RouteDecision(datasource="vectorstore")
    if "web_search" in normalized:
        return RouteDecision(datasource="web_search")
    return RouteDecision(datasource="vectorstore")


def parse_binary_score(text: str) -> BinaryScore:
    """解析 yes/no；含糊输出按 no 处理。"""

    normalized = (text or "").strip().lower()
    if normalized.startswith("yes") or normalized == "y":
        return BinaryScore(binary_score="yes")
    if normalized.startswith("no") or normalized == "n":
        return BinaryScore(binary_score="no")
    return BinaryScore(binary_score="no")
