"""Convert supported source files to Markdown."""

import warnings
from pathlib import Path
from typing import Any


def _create_converter() -> Any:
    try:
        import dotenv
    except ImportError as exc:
        raise RuntimeError(
            "MarkItDown is not installed. Run: python -m pip install -r requirements.txt"
        ) from exc

    original_load_dotenv = dotenv.load_dotenv
    dotenv.load_dotenv = lambda *args, **kwargs: False
    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Couldn't find ffmpeg or avconv.*",
                category=RuntimeWarning,
                module=r"pydub\.utils",
            )
            from markitdown import MarkItDown
    except ImportError as exc:
        raise RuntimeError(
            "MarkItDown is not installed. Run: python -m pip install -r requirements.txt"
        ) from exc
    finally:
        dotenv.load_dotenv = original_load_dotenv
    return MarkItDown()


def convert_source(source: Path, destination: Path) -> str:
    """Convert one supported source file, write Markdown, and return its contents."""
    if not source.is_file():
        raise FileNotFoundError(f"Source file does not exist: {source}")
    # MarkItDown 不支持旧版二进制 Word；提前给出用户可执行的处理方式。
    if source.suffix.lower() == ".doc":
        raise ValueError("暂不支持旧版 Word .doc 文件，请先用 Word 或 WPS 另存为 .docx 后重新上传")

    result = _create_converter().convert(str(source))
    text_content = getattr(result, "text_content", None)
    if not isinstance(text_content, str):
        raise TypeError(f"MarkItDown returned no text_content for: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text_content, encoding="utf-8")
    return text_content
