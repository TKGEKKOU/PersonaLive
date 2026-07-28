from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from rag.llm import get_llm


def rewrite_query(question: str) -> str:
    """把用户问题改写成更适合混合检索的查询。"""

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是知识库检索查询改写器。请把用户问题改写成适合向量检索和关键词检索的短查询。只输出改写后的查询。",
            ),
            ("human", "{question}"),
        ]
    )
    chain = prompt | get_llm() | StrOutputParser()
    rewritten = chain.invoke({"question": question}).strip()
    # 空输出回退原问题，保证后续检索始终有合法查询。
    return rewritten or question
