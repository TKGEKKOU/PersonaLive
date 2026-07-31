"""Embedding client shared by ingestion and retrieval."""

import httpx
from langchain_openai import OpenAIEmbeddings
from functools import lru_cache

from settings import Settings
from ingestion.local_embedding.client import get_managed_embeddings


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


def get_embedding_model(settings: Settings | None = None):
    active = settings or Settings.load()
    if active.embedding_provider == "managed_local":
        return get_managed_embeddings(
            str(active.project_root.resolve()),
            active.embedding_model,
            active.embedding_device,
        )
    return _build_embedding_model(
        active.embedding_api_key,
        active.embedding_base_url,
        active.embedding_model,
        active.embedding_dimensions,
        active.embedding_send_dimensions,
    )


def warm_managed_embedding(settings: Settings | None = None) -> bool:
    """后台预热本地模型，避免第一次 RAG 请求承担模型加载耗时。"""
    active = settings or Settings.load()
    if active.embedding_provider != "managed_local":
        return False
    try:
        get_embedding_model(active).embed_query("模型预热")
        return True
    except Exception:
        return False
