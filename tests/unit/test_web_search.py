from types import SimpleNamespace


def test_bocha_provider_normalizes_results_to_documents(monkeypatch):
    import rag.web_search as web_search

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "webPages": {
                        "value": [
                            {
                                "name": "Weather report",
                                "url": "https://weather.example/report",
                                "summary": "Tomorrow: light rain, 25C to 32C.",
                                "siteName": "Weather Service",
                                "datePublished": "2026-07-28T08:00:00+08:00",
                            }
                        ]
                    }
                }
            }

    class FakeSession:
        trust_env = True

        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

        def close(self):
            return None

    monkeypatch.setattr(web_search.requests, "Session", FakeSession)

    documents = web_search.search_web_documents("bocha", "test-key", "tomorrow weather", recent=True)

    assert captured["url"] == "https://api.bocha.cn/v1/web-search"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"] == {"query": "tomorrow weather", "freshness": "oneDay", "summary": True, "count": 4}
    assert documents[0].page_content == "Tomorrow: light rain, 25C to 32C."
    assert documents[0].metadata == {
        "filename": "web_result_1",
        "source": "https://weather.example/report",
        "title": "Weather report",
        "published_date": "2026-07-28T08:00:00+08:00",
        "site_name": "Weather Service",
    }


def test_custom_provider_uses_its_configured_bocha_compatible_endpoint(monkeypatch):
    import rag.web_search as web_search

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"webPages": {"value": []}}}

    class FakeSession:
        trust_env = True

        def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

        def close(self):
            return None

    monkeypatch.setattr(web_search.requests, "Session", FakeSession)

    documents = web_search.search_web_documents(
        "custom", "test-key", "latest news", base_url="https://search.example/v1/web-search"
    )

    assert documents == []
    assert captured["url"] == "https://search.example/v1/web-search"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
