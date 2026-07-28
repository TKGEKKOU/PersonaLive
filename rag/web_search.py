from langchain_core.documents import Document
import requests

from settings import Settings


BOCHA_WEB_SEARCH_ENDPOINT = "https://api.bocha.cn/v1/web-search"


def _documents_from_tavily(result: dict) -> list[Document]:
    return [
        Document(
            page_content=item.get("content", ""),
            metadata={
                "filename": f"web_result_{index + 1}",
                "source": item.get("url", ""),
                "title": item.get("title", ""),
                "published_date": item.get("published_date", ""),
            },
        )
        for index, item in enumerate(result.get("results", []))
    ]


def _search_tavily(api_key: str, question: str, recent: bool, _base_url: str = "") -> list[Document]:
    session = requests.Session()
    session.trust_env = False
    try:
        from tavily import TavilyClient

        client = TavilyClient(api_key=api_key, session=session)
        options = {"query": question, "max_results": 4, "search_depth": "advanced"}
        if recent:
            options["time_range"] = "day"
        return _documents_from_tavily(client.search(**options))
    finally:
        session.close()


def _search_bocha_compatible(endpoint: str, api_key: str, question: str, recent: bool) -> list[Document]:
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "query": question,
                "freshness": "oneDay" if recent else "noLimit",
                "summary": True,
                "count": 4,
            },
            timeout=15,
        )
        response.raise_for_status()
        items = response.json().get("data", {}).get("webPages", {}).get("value", [])
        return [
            Document(
                page_content=item.get("summary") or item.get("snippet", ""),
                metadata={
                    "filename": f"web_result_{index + 1}",
                    "source": item.get("url", ""),
                    "title": item.get("name", ""),
                    "published_date": item.get("datePublished") or item.get("dateLastCrawled", ""),
                    "site_name": item.get("siteName", ""),
                },
            )
            for index, item in enumerate(items)
        ]
    finally:
        session.close()


def _search_bocha(api_key: str, question: str, recent: bool, _base_url: str = "") -> list[Document]:
    return _search_bocha_compatible(BOCHA_WEB_SEARCH_ENDPOINT, api_key, question, recent)


def _search_custom(api_key: str, question: str, recent: bool, base_url: str) -> list[Document]:
    if not base_url:
        return []
    return _search_bocha_compatible(base_url, api_key, question, recent)


_SEARCH_PROVIDERS = {"tavily": _search_tavily, "bocha": _search_bocha, "custom": _search_custom}


def search_web_documents(
    provider: str, api_key: str, question: str, recent: bool = False, base_url: str = ""
) -> list[Document]:
    """Run a configured provider and normalize its results for RAG consumers."""
    search = _SEARCH_PROVIDERS.get(provider)
    if not search or not api_key:
        return []
    try:
        return search(api_key, question, recent, base_url)
    except Exception:
        return []


def web_search_documents(question: str, recent: bool = False) -> list[Document]:
    """Use the locally configured provider only when web fallback is enabled."""
    settings = Settings.load()
    if not settings.enable_web_fallback:
        return []
    return search_web_documents(
        settings.web_search_provider,
        settings.web_search_api_key,
        question,
        recent,
        settings.web_search_base_url,
    )
