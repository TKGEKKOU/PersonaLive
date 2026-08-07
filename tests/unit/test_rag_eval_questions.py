import pytest

from rag.eval.question_generator import (
    _parse_json_array,
    generate_question_set,
    load_chunks,
)
from rag.eval.runner import PROBE_QUESTIONS


class FakeLLM:
    def __init__(self, responses: dict[str, str]) -> None:
        self.responses = responses

    def invoke(self, messages):
        text = "\n".join(str(message.content) for message in messages)
        for key, value in self.responses.items():
            if key in text:
                return type("R", (), {"content": value})()
        return type("R", (), {"content": "[]"})()


def _question_payload(pairs: list[tuple[int, str]]) -> str:
    items = ",".join(f'{{"index": {index}, "question": "{question}"}}' for index, question in pairs)
    return f"[{items}]"


def test_parse_json_array_handles_fences_and_invalid():
    assert _parse_json_array('[{"index": 0, "question": "q"}]') == [
        {"index": 0, "question": "q"}
    ]
    assert _parse_json_array('```json\n["a"]\n```') == ["a"]
    with pytest.raises(ValueError):
        _parse_json_array("not json")
    with pytest.raises(ValueError):
        _parse_json_array('{"a": 1}')


def test_generate_question_set_fast_tier_composition():
    chunks = [("c0", "生日是 3 月 14 日。"), ("c1", "口头禅是「没问题」。")]
    llm = FakeLLM(
        {
            "复杂问题": (
                '[{"question": "综合问题A", "chunk_ids": [0, 1]},'
                '{"question": "综合问题B", "chunk_ids": [0, 1]}]'
            ),
            "片段列表": _question_payload([(0, "生日是哪天？"), (1, "口头禅是什么？")]),
        }
    )

    rows = generate_question_set(chunks, llm, tier="fast")

    assert len(rows) == 5
    content = [row for row in rows if not row.get("_probe")]
    assert len(content) == 4
    assert sum(1 for row in content if row.get("_complex")) == 2
    assert sum(1 for row in content if not row.get("_complex")) == 2
    probes = [row for row in rows if row.get("_probe")]
    assert len(probes) == 1
    assert probes[0]["question"] == PROBE_QUESTIONS[0]
    complex_row = next(row for row in content if row.get("_complex"))
    assert complex_row["expected_chunk_ids"] == ["c0", "c1"]


def test_generate_question_set_standard_tier_fills_quota():
    chunks = [("c0", "内容" * 10), ("c1", "内容" * 10)]
    pairs = [(0, f"q0-{n}") for n in range(3)] + [(1, f"q1-{n}") for n in range(3)]
    llm = FakeLLM(
        {
            "复杂问题": (
                '[{"question": "复杂1", "chunk_ids": [0, 1]},'
                '{"question": "复杂2", "chunk_ids": [0, 1]},'
                '{"question": "复杂3", "chunk_ids": [0, 1]}]'
            ),
            "片段列表": _question_payload(pairs),
        }
    )

    rows = generate_question_set(chunks, llm, tier="standard")

    assert len(rows) == 10
    assert sum(1 for row in rows if row.get("_probe")) == 2
    assert sum(1 for row in rows if row.get("_complex")) == 3
    assert sum(1 for row in rows if not row.get("_probe") and not row.get("_complex")) == 5


def test_generate_question_set_empty_chunks_returns_only_probes():
    rows = generate_question_set([], FakeLLM({}), tier="standard")
    assert len(rows) == 2
    assert all(row.get("_probe") for row in rows)


def test_generate_question_set_dedupes_and_skips_failed_batches():
    chunks = [("c0", "内容" * 20), ("c1", "内容" * 20)]

    class FlakyLLM:
        calls = 0

        def invoke(self, messages):
            text = "\n".join(str(message.content) for message in messages)
            if "复杂问题" in text:
                return type("R", (), {"content": "[]"})()
            if "片段列表" not in text:
                return type("R", (), {"content": "[]"})()
            self.calls += 1
            if self.calls == 1:
                return type(
                    "R",
                    (),
                    {
                        "content": '[{"index": 0, "question": "重复问题"}, {"index": 0, "question": "重复问题"}]'
                    },
                )()
            return type("R", (), {"content": "不是 JSON"})()

    rows = generate_question_set(chunks, FlakyLLM(), tier="fast")
    content = [row for row in rows if not row.get("_probe")]
    assert sum(1 for row in content if not row.get("_complex")) == 1
    assert content[0]["question"] == "重复问题"


def test_load_chunks_filters_short_and_maps_fields(monkeypatch):
    class FakeClient:
        def query(self, **kwargs):
            return [
                {"chunk_id": "c1", "text": "   "},
                {"chunk_id": "c2", "text": "有效内容" * 10},
                {"chunk_id": "c3", "text": "短"},
            ]

    class FakeStore:
        settings = type("S", (), {"collection_name": "col"})()

        def connect(self):
            return self

        def client(self):
            return FakeClient()

    monkeypatch.setattr("rag.eval.question_generator.MilvusRagStore", FakeStore)
    chunks = load_chunks("local-default", ["space-a"])
    assert chunks == [("c2", "有效内容" * 10)]


def test_generate_questions_for_persona_reuses_cache(monkeypatch, tmp_path):
    import rag.eval.question_generator as generator

    out_path = tmp_path / "questions.jsonl"
    calls = {"llm": 0, "status": []}

    def fake_load_chunks(workspace_id, knowledge_space_ids, max_chunks=None):
        return [("c0", "生日是 3 月 14 日。"), ("c1", "口头禅是「没问题」。")]

    def fake_generate(chunks, llm, total=None, tier="fast", status=None):
        calls["llm"] += 1
        if status:
            status("生成中")
        return [
            {"question": "生日是哪天？", "expected_chunk_ids": ["c0"], "reference_answer": None},
            {"question": "口头禅是什么？", "expected_chunk_ids": ["c1"], "reference_answer": None},
            {"question": "综合题", "expected_chunk_ids": ["c0", "c1"], "reference_answer": None, "_complex": True},
            {"question": "综合题2", "expected_chunk_ids": ["c0", "c1"], "reference_answer": None, "_complex": True},
            {"question": "无关题", "expected_chunk_ids": [], "reference_answer": None, "_probe": True},
        ]

    monkeypatch.setattr(generator, "load_chunks", fake_load_chunks)
    monkeypatch.setattr(generator, "generate_question_set", fake_generate)
    monkeypatch.setattr(generator, "get_llm", lambda: object())

    def status(text):
        calls["status"].append(text)

    first = generator.generate_questions_for_persona(
        persona_id="p1",
        workspace_id="local-default",
        knowledge_space_ids=["s1"],
        out_path=out_path,
        tier="fast",
        status=status,
    )
    second = generator.generate_questions_for_persona(
        persona_id="p1",
        workspace_id="local-default",
        knowledge_space_ids=["s1"],
        out_path=out_path,
        tier="fast",
        status=status,
    )

    assert first == second == out_path
    assert calls["llm"] == 1  # 第二次命中缓存，不再调用 LLM
    assert "知识未变化，复用已生成的问题集" in calls["status"]
