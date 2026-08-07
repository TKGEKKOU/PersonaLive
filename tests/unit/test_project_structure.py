import tomllib
from pathlib import Path


def test_application_uses_flat_domain_packages():
    root = Path(__file__).resolve().parents[2]
    assert not (root / "src" / "persona_live").exists()
    assert (root / "settings.py").is_file()
    for package in ("app", "persona", "ingestion", "rag", "agents", "voice", "live"):
        assert (root / package / "__init__.py").is_file()


def test_settings_module_is_included_in_distribution():
    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as pyproject_file:
        pyproject = tomllib.load(pyproject_file)

    assert pyproject["tool"]["setuptools"]["py-modules"] == ["settings"]
