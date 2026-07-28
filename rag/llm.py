import httpx
from langchain_openai import ChatOpenAI
from functools import lru_cache

from settings import Settings


# 这里使用 OpenAI-compatible 接口。只要服务兼容 OpenAI Chat Completions，就可以替换 base_url 和 model。
@lru_cache(maxsize=8)
def _build_llm(api_key: str, base_url: str, model: str) -> ChatOpenAI:
    return ChatOpenAI(
        api_key=api_key,
        base_url=base_url,
        model=model,
        temperature=0,
        http_client=httpx.Client(trust_env=False, timeout=60),
    )


def get_llm(settings: Settings | None = None) -> ChatOpenAI:
    active = settings or Settings.load()
    return _build_llm(active.openai_api_key, active.openai_base_url, active.openai_model)
