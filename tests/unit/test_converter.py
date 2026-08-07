from pathlib import Path
from types import SimpleNamespace

import pytest


def test_convert_source_writes_and_returns_markdown(tmp_path, monkeypatch):
    from ingestion.converter import convert_source
    import ingestion.converter as converter_module

    source = tmp_path / "source.txt"
    source.write_text("source", encoding="utf-8")
    destination = tmp_path / "converted.md"

    class FakeConverter:
        def convert(self, path: str) -> SimpleNamespace:
            assert Path(path) == source
            return SimpleNamespace(text_content="# Converted\n\nMarkdown output.")

    monkeypatch.setattr(converter_module, "_create_converter", lambda: FakeConverter())

    converted = convert_source(source, destination)

    assert converted == "# Converted\n\nMarkdown output."
    assert destination.read_text(encoding="utf-8") == converted


def test_convert_source_rejects_legacy_doc_with_actionable_message(tmp_path):
    from ingestion.converter import convert_source

    source = tmp_path / "legacy.doc"
    source.write_bytes(b"legacy word content")

    with pytest.raises(ValueError, match=r"旧版 Word.*\.docx"):
        convert_source(source, tmp_path / "converted.md")
