"""rag.llm.invoke_llm 对外层瞬时故障（429/5xx）的重试与失败语义。"""

import pytest
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda


class TransientServiceError(RuntimeError):
    status_code = 503


PROMPT = ChatPromptTemplate.from_messages([("human", "{question}")])


def _flaky_model(attempts, responses):
    """按调用次数依次抛错或返回文本的可运行假模型。"""

    def run(_):
        index = attempts[0]
        attempts[0] += 1
        if index < len(responses):
            raise responses[index]
        return "ok"

    return RunnableLambda(run)


def test_invoke_llm_retries_transient_error_and_succeeds(monkeypatch):
    from rag.llm import invoke_llm

    attempts = [0]
    monkeypatch.setattr("rag.llm.get_llm", lambda: _flaky_model(attempts, [TransientServiceError()] * 2))
    monkeypatch.setattr("rag.llm._sleep", lambda seconds: None)

    result = invoke_llm(PROMPT, {"question": "q"})

    assert result == "ok"
    assert attempts[0] == 3


def test_invoke_llm_reraises_after_retries_exhausted(monkeypatch):
    from rag.llm import invoke_llm

    attempts = [0]
    monkeypatch.setattr("rag.llm.get_llm", lambda: _flaky_model(attempts, [TransientServiceError()] * 10))
    monkeypatch.setattr("rag.llm._sleep", lambda seconds: None)

    with pytest.raises(TransientServiceError):
        invoke_llm(PROMPT, {"question": "q"})
    assert attempts[0] == 3


def test_invoke_llm_does_not_retry_non_transient_error(monkeypatch):
    from rag.llm import invoke_llm

    attempts = [0]
    monkeypatch.setattr("rag.llm.get_llm", lambda: _flaky_model(attempts, [ValueError("boom")]))
    monkeypatch.setattr("rag.llm._sleep", lambda seconds: None)

    with pytest.raises(ValueError, match="boom"):
        invoke_llm(PROMPT, {"question": "q"})
    assert attempts[0] == 1
