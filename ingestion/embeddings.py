"""Embedding client shared by ingestion and retrieval."""

import httpx
from langchain_openai import OpenAIEmbeddings
from functools import lru_cache

from settings import Settings


def embedding_options(settings: Settings) -> dict:
    options = {
        "openai_api_key": settings.embedding_api_key,
        "openai_api_base": settings.embedding_base_url,
        "model": settings.embedding_model,
        "chunk_size": 10,
        "tiktoken_enabled": False,
        "check_embedding_ctx_length": False,
        "http_client": httpx.Client(trust_env=False, timeout=60),
    }
    if settings.embedding_send_dimensions:
        options["dimensions"] = settings.embedding_dimensions
    return options


@lru_cache(maxsize=8)
def _build_embedding_model(
    api_key: str,
    base_url: str,
    model: str,
    dimensions: int,
    send_dimensions: bool,
) -> OpenAIEmbeddings:
    options = {
        "openai_api_key": api_key,
        "openai_api_base": base_url,
        "model": model,
        "chunk_size": 10,
        "tiktoken_enabled": False,
        "check_embedding_ctx_length": False,
        "http_client": httpx.Client(trust_env=False, timeout=60),
    }
    if send_dimensions:
        options["dimensions"] = dimensions
    return OpenAIEmbeddings(**options)


def get_embedding_model(settings: Settings | None = None) -> OpenAIEmbeddings:
    active = settings or Settings.load()
    return _build_embedding_model(
        active.embedding_api_key,
        active.embedding_base_url,
        active.embedding_model,
        active.embedding_dimensions,
        active.embedding_send_dimensions,
    )
