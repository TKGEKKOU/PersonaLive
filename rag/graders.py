"""RAG 质量门使用的结构化评分器。

评分器只提出相关片段、置信度和纠错建议；是否重试、联网或终止仍由
adaptive_graph.py 中的确定性路由与计数器决定。
"""

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from rag.llm import get_llm
from rag.output_parsers import (
    AnswerQualityScore,
    BatchDocumentScore,
    BinaryScore,
    parse_answer_quality_score,
    parse_batch_document_score,
    parse_binary_score,
)


def _invoke_text(prompt: ChatPromptTemplate, values: dict) -> str:
    chain = prompt | get_llm() | StrOutputParser()
    return chain.invoke(values)


# 批量证据评分（默认路径）：一次 LLM 调用同时评估所有候选片段。
# 相比逐片段调用，显著降低 token 与延迟；单片段截断 4000 字符防止上下文膨胀。
# 输出 JSON：relevant_ids（直接支持答案的片段下标）+ confidence（0~1）。
def grade_retrieved_documents(question: str, documents: list) -> BatchDocumentScore:
    """一次调用筛选候选片段并评估证据置信度。"""

    if not documents:
        return BatchDocumentScore()

    # 限制单个候选长度，防止评分上下文异常膨胀。
    numbered_documents = "\n\n".join(
        f"[{index}] {(getattr(document, 'page_content', '') or '')[:4000]}"
        for index, document in enumerate(documents)
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是知识库检索评分器。一次评估所有候选片段，只选择能直接帮助回答问题的片段。"
                "只输出 JSON，不要输出 Markdown："
                '{{"relevant_ids":[0],"confidence":0.0,"reason":"简短原因"}}。'
                "confidence 表示所选证据足以支持答案的程度，范围 0 到 1。"
                "仅有词语重合但不能回答问题的片段必须排除。",
            ),
            ("human", "用户问题：\n{question}\n\n候选片段：\n{documents}"),
        ]
    )
    raw_score = _invoke_text(prompt, {"question": question, "documents": numbered_documents})
    return parse_batch_document_score(raw_score, len(documents))


# 回答质量门：检查 grounded（回答是否完全由资料支持）与 useful（是否真正
# 解决问题），并让模型给出 correction_action（regenerate / retrieve_again /
# web_search / no_answer）。该动作只作为建议，是否执行由外层计数器决定
# （见 adaptive_graph.decide_quality），防止模型自导自演无限循环。
def grade_answer_quality(question: str, documents_text: str, answer: str) -> AnswerQualityScore:
    """检查事实接地、问题覆盖和下一步纠错动作。"""

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是回答质量检查器。检查回答是否完全由资料支持、是否真正解决问题，并给出纠错动作。"
                "只输出 JSON，不要输出 Markdown："
                '{{"grounded":true,"useful":true,"missing_points":[],'
                '"unsupported_claims":[],"correction_action":"regenerate"}}。'
                "correction_action 只能是 regenerate、retrieve_again、web_search、no_answer。",
            ),
            (
                "human",
                "用户问题：\n{question}\n\n参考资料：\n{documents}\n\n待检查回答：\n{answer}",
            ),
        ]
    )
    raw_score = _invoke_text(
        prompt,
        {"question": question, "documents": documents_text, "answer": answer},
    )
    # 解析失败时按未通过处理。
    return parse_answer_quality_score(raw_score)


def grade_retrieved_document(question: str, document_text: str) -> BinaryScore:
    """判断单个检索片段是否和问题相关。

    这是旧评分接口，仅为兼容保留；默认模式使用一次批量评分，避免按片段
    重复增加 LLM 调用次数；simple 模式也不会调用此评分器。
    """

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "判断检索文档是否与用户问题相关。只输出 yes 或 no，不要解释。"),
            ("human", "用户问题：\n{question}\n\n检索文档：\n{document}"),
        ]
    )
    return parse_binary_score(_invoke_text(prompt, {"question": question, "document": document_text}))


def grade_generation_grounding(documents_text: str, answer: str) -> BinaryScore:
    """判断回答是否基于参考资料。

    这一步用于降低幻觉风险。注意它不是绝对可靠的安全机制，只是一个自动化质量检查。
    """

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "判断回答是否被参考资料支持。只输出 yes 或 no，不要解释。"),
            ("human", "参考资料：\n{documents}\n\n回答：\n{answer}"),
        ]
    )
    return parse_binary_score(_invoke_text(prompt, {"documents": documents_text, "answer": answer}))


def grade_answer_usefulness(question: str, answer: str) -> BinaryScore:
    """判断回答是否解决了用户问题。"""

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "判断回答是否解决了用户问题。只输出 yes 或 no，不要解释。"),
            ("human", "用户问题：\n{question}\n\n回答：\n{answer}"),
        ]
    )
    return parse_binary_score(_invoke_text(prompt, {"question": question, "answer": answer}))
