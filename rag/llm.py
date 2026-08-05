import logging
import time
from functools import lru_cache

import httpx
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

from settings import Settings


logger = logging.getLogger(__name__)

# 可重试的瞬时故障：限流与 5xx 服务端错误；其他异常（参数错误、超时等）不重试。
TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MAX_OUTER_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.0

# 模型服务不可用时的统一降级提示，供 Agent/RAG 层直接返回给用户。
LLM_UNAVAILABLE_MESSAGE = "模型服务暂时不可用，请稍后重试。"


def is_transient_provider_error(error: Exception) -> bool:
    """判断异常是否属于可重试的瞬时服务故障（OpenAI-compatible 5xx/429）。"""

    return getattr(error, "status_code", None) in TRANSIENT_STATUS_CODES


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


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


def invoke_llm(prompt, values: dict) -> str:
    """执行 prompt -> LLM -> 文本 链，并对瞬时故障做指数退避重试。

    模型内部已有默认重试；这里再兜一层，吸收偶发的 503 服务繁忙等抖动。
    重试耗尽后保留原始异常，由上层决定降级还是上报。
    """

    chain = prompt | get_llm() | StrOutputParser()
    for attempt in range(MAX_OUTER_RETRIES + 1):
        try:
            return chain.invoke(values)
        except Exception as exc:
            if attempt >= MAX_OUTER_RETRIES or not is_transient_provider_error(exc):
                raise
            delay = RETRY_BACKOFF_SECONDS * (2**attempt)
            logger.warning("LLM 瞬时故障（%s），%.1fs 后重试 %d/%d", exc, delay, attempt + 1, MAX_OUTER_RETRIES)
            _sleep(delay)
    raise AssertionError("unreachable")
