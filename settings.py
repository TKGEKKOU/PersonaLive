from dataclasses import dataclass
import json
from pathlib import Path

from dotenv import dotenv_values

SUPPORTED_WEB_SEARCH_PROVIDERS = frozenset({"off", "tavily", "bocha", "custom"})


@dataclass(frozen=True)
class Settings:
    project_root: Path
    app_host: str
    app_port: int
    workspace_id: str
    mysql_host: str
    mysql_port: int
    mysql_database: str
    mysql_user: str
    mysql_password: str
    milvus_uri: str
    milvus_user: str
    milvus_password: str
    collection_name: str
    embedding_dimensions: int
    embedding_send_dimensions: bool
    rag_pipeline: str
    confidence_threshold: float
    max_rewrite_count: int
    max_generation_retry: int
    max_upload_mb: int
    enable_web_fallback: bool
    web_search_provider: str
    web_search_api_key: str
    web_search_base_url: str
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    embedding_api_key: str
    embedding_base_url: str
    embedding_model: str

    @classmethod
    def load(cls, root: Path | None = None) -> "Settings":
        """合并基础设施配置与 UI 配置，并返回不可变的运行时快照。

        `.env` 只负责 MySQL、Milvus、监听地址和 RAG 控制参数；模型、Embedding
        与联网搜索凭据来自 `data/local_settings.json`，避免页面保存后仍被旧环境
        变量覆盖。
        """

        project_root = root or Path(__file__).resolve().parent
        values = dotenv_values(project_root / ".env")
        get = lambda name, default: str(values.get(name) or default)
        local_path = project_root / "data" / "local_settings.json"
        # 本地设置文件损坏时回退为空配置，让设置页仍可启动并修正配置。
        try:
            local_values = json.loads(local_path.read_text(encoding="utf-8")) if local_path.is_file() else {}
        except (OSError, json.JSONDecodeError):
            local_values = {}
        local_get = lambda name, default: str(local_values.get(name) or default)
        local_bool = lambda name, default: str(local_values.get(name, default)).lower() in {"1", "true", "yes", "on"}
        # 兼容旧版 Tavily 开关；保存新格式后统一以 provider 是否为 off 判断。
        legacy_web_enabled = local_bool("enable_web_fallback", False)
        web_search_provider = local_get("web_search_provider", "") or ("tavily" if legacy_web_enabled else "off")
        if web_search_provider not in SUPPORTED_WEB_SEARCH_PROVIDERS:
            web_search_provider = "off"
        web_search_api_key = local_get("web_search_api_key", local_get("tavily_api_key", ""))
        web_search_base_url = local_get("web_search_base_url", "")
        return cls(
            project_root=project_root,
            app_host=get("APP_HOST", "127.0.0.1"),
            app_port=int(get("APP_PORT", "8001")),
            workspace_id="local-default",
            mysql_host=get("MYSQL_HOST", "127.0.0.1"),
            mysql_port=int(get("MYSQL_PORT", "13306")),
            mysql_database=get("MYSQL_DATABASE", "personalive"),
            mysql_user=get("MYSQL_USER", "personalive"),
            mysql_password=get("MYSQL_PASSWORD", ""),
            milvus_uri=get("MILVUS_URI", "http://127.0.0.1:19530"),
            milvus_user=get("MILVUS_USER", ""),
            milvus_password=get("MILVUS_PASSWORD", ""),
            collection_name=get("COLLECTION_NAME", "personalive_knowledge_v1"),
            embedding_dimensions=int(local_values.get("embedding_dimensions", 512)),
            embedding_send_dimensions=local_bool("embedding_send_dimensions", True),
            rag_pipeline=get("RAG_PIPELINE", "default").lower(),
            confidence_threshold=float(get("DEFAULT_CONFIDENCE_THRESHOLD", "0.75")),
            max_rewrite_count=int(get("MAX_REWRITE_COUNT", "2")),
            max_generation_retry=int(get("MAX_GENERATION_RETRY", "2")),
            max_upload_mb=int(get("MAX_UPLOAD_MB", "50")),
            enable_web_fallback=web_search_provider != "off",
            web_search_provider=web_search_provider,
            web_search_api_key=web_search_api_key,
            web_search_base_url=web_search_base_url,
            openai_api_key=local_get("openai_api_key", ""),
            openai_base_url=local_get("openai_base_url", ""),
            openai_model=local_get("openai_model", ""),
            embedding_api_key=local_get("embedding_api_key", ""),
            embedding_base_url=local_get("embedding_base_url", ""),
            embedding_model=local_get("embedding_model", ""),
        )
