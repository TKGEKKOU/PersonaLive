import json

from langchain_core.prompts import PromptTemplate

from rag.llm import invoke_llm


PROMPT = PromptTemplate(
    template=(
        "你是一个严谨的知识库问答助手。请只根据给定的参考资料回答问题。\n"
        "请始终使用指定角色的身份、语气和视角回答，但不要改变资料事实。\n"
        "知识问答只输出一个自然段，控制在300字以内；先给结论，不要复述资料。\n"
        "如果参考资料不足以回答，请直接说明“资料中没有足够信息”。\n\n"
        "角色：{persona_name}\n角色设定：{persona_profile}\n\n"
        "问题：{question}\n\n"
        "参考资料：\n{context}\n\n"
        "回答："
    ),
    input_variables=["question", "context", "persona_name", "persona_profile"],
)

CORRECTION_PROMPT = PromptTemplate(
    template=(
        "你是一个严谨的知识库问答助手。上一版回答没有通过质量检查，请根据反馈修订。\n"
        "请始终使用指定角色的身份、语气和视角回答，但不要改变资料事实。\n"
        "修订后的知识回答仍只输出一个自然段，控制在300字以内。\n"
        "只能使用给定参考资料；删除没有资料支持的结论；资料不足时明确说明。\n\n"
        "角色：{persona_name}\n角色设定：{persona_profile}\n\n"
        "问题：{question}\n\n"
        "参考资料：\n{context}\n\n"
        "上一版回答：\n{previous_answer}\n\n"
        "质量检查反馈：\n{correction_feedback}\n\n"
        "修订后的回答："
    ),
    input_variables=["question", "context", "persona_name", "persona_profile", "previous_answer", "correction_feedback"],
)


def format_documents(documents) -> str:
    if not documents:
        return "无参考资料。"
    return "\n\n".join(document.page_content for document in documents)


def _invoke_generation(prompt: PromptTemplate, values: dict) -> str:
    return invoke_llm(prompt, values)


def generate_answer(
    question: str,
    documents,
    previous_answer: str = "",
    correction_feedback: str = "",
    persona_name: str = "角色",
    persona_profile: dict | None = None,
) -> str:
    """根据当前 state 选择首次生成或携带反馈的纠错生成。"""

    values = {
        "question": question,
        "context": format_documents(documents),
        "persona_name": persona_name or "角色",
        "persona_profile": json.dumps(persona_profile or {}, ensure_ascii=False),
    }
    if correction_feedback:
        values.update(
            {
                "previous_answer": previous_answer,
                "correction_feedback": correction_feedback,
            }
        )
        return _invoke_generation(CORRECTION_PROMPT, values)
    return _invoke_generation(PROMPT, values)
