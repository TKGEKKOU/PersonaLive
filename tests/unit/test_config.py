import json
from pathlib import Path

from settings import Settings


def test_provider_settings_come_from_local_settings_not_env(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "APP_PORT=8123\nOPENAI_API_KEY=ignored-env-key\nOPENAI_MODEL=ignored-env-model\n",
        encoding="utf-8",
    )
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "local_settings.json").write_text(
        json.dumps({
            "openai_api_key": "frontend-key",
            "openai_model": "frontend-model",
            "embedding_send_dimensions": False,
        }),
        encoding="utf-8",
    )
    settings = Settings.load(tmp_path)
    assert settings.app_host == "127.0.0.1"
    assert settings.app_port == 8123
    assert settings.workspace_id == "local-default"
    assert settings.openai_api_key == "frontend-key"
    assert settings.openai_model == "frontend-model"
    assert settings.embedding_send_dimensions is False


def test_env_provider_values_are_ignored_without_frontend_settings(tmp_path: Path):
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=ignored\nEMBEDDING_API_KEY=ignored\nTAVILY_API_KEY=ignored\n",
        encoding="utf-8",
    )

    settings = Settings.load(tmp_path)

    assert settings.openai_api_key == ""
    assert settings.embedding_api_key == ""
    assert settings.web_search_provider == "off"
    assert settings.web_search_api_key == ""


def test_embedding_options_omit_dimensions_when_disabled(tmp_path: Path):
    from ingestion.embeddings import embedding_options

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "local_settings.json").write_text(json.dumps({
        "embedding_api_key": "test",
        "embedding_base_url": "https://example.com/v1",
        "embedding_model": "custom-model",
        "embedding_dimensions": 768,
        "embedding_send_dimensions": False,
    }), encoding="utf-8")

    options = embedding_options(Settings.load(tmp_path))

    assert options["model"] == "custom-model"
    assert "dimensions" not in options


def test_runtime_modules_import_without_api_keys():
    import ingestion.embeddings
    import rag.llm

    assert callable(ingestion.embeddings.get_embedding_model)
    assert callable(rag.llm.get_llm)
