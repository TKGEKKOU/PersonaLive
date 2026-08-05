from typing import Literal

from langchain_core.prompts import ChatPromptTemplate

from rag.llm import invoke_llm


InteractionMode = Literal["conversation", "capability", "knowledge", "web"]

# 路由信号分四类，按"成本从低到高"依次匹配：
# 关键词命中直接返回（零 LLM 调用）；都不命中才走 LLM 兜底分类（classify_ambiguous）。
# 这种"规则优先、LLM 兜底"的分层路由把高频简单意图的延迟与成本降到最低。
CAPABILITY_SIGNALS = ("tool", "工具", "能力", "能调用", "会调用", "可以调用")
LOCAL_SIGNALS = ("资料", "文档", "知识库", "上传", "报告", "根据内容", "根据设定")
CONVERSATION_SIGNALS = (
    "你好", "您好", "嗨", "在吗", "早上好", "晚上好", "谢谢", "再见",
    "你是谁", "介绍自己", "你喜欢", "你讨厌", "你觉得", "你想", "你今天",
    "陪我", "聊聊", "讲个笑话", "心情",
)
REALTIME_SIGNALS = ("天气", "新闻", "当前价格", "汇率", "最新政策", "实时", "today", "weather", "news")

ROUTER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "把用户消息分类为 conversation 或 knowledge。"
            "角色闲聊、情绪、偏好、创作和一般交流属于 conversation；"
            "需要从角色资料中查事实、设定、经历或专业内容属于 knowledge。"
            "只输出一个分类词。",
        ),
        ("human", "{question}"),
    ]
)


def classify_ambiguous(question: str) -> Literal["conversation", "knowledge"]:
    # LLM 兜底：规则无法判定的模糊请求交给模型二分类；失败时保守归为 knowledge，
    # 保证资料类问题不会被错误分流到闲聊。
    try:
        value = invoke_llm(ROUTER_PROMPT, {"question": question}).strip().lower()
        return "conversation" if value == "conversation" else "knowledge"
    except Exception:
        return "knowledge"


def route_interaction(question: str, enable_web_search: bool) -> InteractionMode:
    # 路由优先级：能力询问 > 本地资料（knowledge）> 闲聊 > 实时信息（需联网开关）> LLM 兜底。
    normalized = (question or "").strip().lower()
    if any(signal in normalized for signal in CAPABILITY_SIGNALS):
        return "capability"
    if any(signal in normalized for signal in LOCAL_SIGNALS):
        return "knowledge"
    if any(signal in normalized for signal in CONVERSATION_SIGNALS):
        return "conversation"
    if enable_web_search and any(signal in normalized for signal in REALTIME_SIGNALS):
        return "web"
    return classify_ambiguous(question)
