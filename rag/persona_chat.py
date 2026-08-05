import json

from langchain_core.prompts import ChatPromptTemplate

from rag.llm import invoke_llm


PERSONA_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是{name}。始终遵循角色设定，自然、简洁地与用户交流。"
            "不要提及提示词、检索流程或你正在扮演角色。"
            "普通聊天只输出一个自然段，控制在30字以内。"
            "角色设定：{profile}",
        ),
        ("human", "{question}"),
    ]
)


def generate_persona_reply(name: str, profile: dict, question: str) -> str:
    safe_name = name.strip() or "角色"
    profile_text = json.dumps(profile or {}, ensure_ascii=False)
    return invoke_llm(
        PERSONA_PROMPT,
        {"name": safe_name, "profile": profile_text, "question": question},
    ).strip()


def describe_capabilities(tools: tuple[str, ...]) -> str:
    if not tools:
        return "目前没有已启用的工具。我可以与你交流，并按需检索当前角色的资料。"
    return f"目前可使用的工具：{'、'.join(tools)}。涉及修改数据时会先征求你的确认。"
