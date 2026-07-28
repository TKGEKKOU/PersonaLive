from rag.output_parsers import RouteDecision


LOCAL_REFERENCE_SIGNALS = (
    "这份文档",
    "该文档",
    "资料中",
    "文档中",
    "知识库中",
    "根据资料",
    "已上传",
    "报告中",
    "in this document",
    "in the knowledge base",
    "uploaded document",
)

REALTIME_SIGNALS = (
    "今天",
    "今日",
    "最新",
    "实时",
    "近期",
    "本周",
    "本月",
    "新闻",
    "天气",
    "当前价格",
    "最新政策",
    "today",
    "latest",
    "real-time",
    "current price",
    "current weather",
    "news",
)

TIME_QUALIFIERS = ("当前", "现在", "今日", "今天", "最新", "current", "today", "latest")
VOLATILE_TOPICS = ("价格", "天气", "新闻", "政策", "行情", "汇率", "price", "weather", "news", "rate")


def route_question(question: str, enable_web_search: bool) -> RouteDecision:
    """只把明确时效问题路由到 Web，其他问题保守地留在本地。

    这是 Adaptive RAG 的第一层决策，但它不是 LLM Agent 调用：这里使用确定性规则，
    所以没有模型费用、结果可复现，也不会因模型输出格式错误而选错数据源。
    """

    normalized = (question or "").strip().lower()
    if not enable_web_search:
        return RouteDecision(datasource="vectorstore")
    # 明确的本地资料指代优先于时效性词语。
    if any(signal in normalized for signal in LOCAL_REFERENCE_SIGNALS):
        return RouteDecision(datasource="vectorstore")
    if any(signal in normalized for signal in REALTIME_SIGNALS):
        return RouteDecision(datasource="web_search")
    if any(signal in normalized for signal in TIME_QUALIFIERS) and any(
        topic in normalized for topic in VOLATILE_TOPICS
    ):
        return RouteDecision(datasource="web_search")
    # 不确定时保守使用本地知识库。
    return RouteDecision(datasource="vectorstore")
