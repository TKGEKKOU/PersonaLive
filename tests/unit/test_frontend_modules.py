from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_script(name: str) -> str:
    return (ROOT / "static" / "js" / f"{name}.js").read_text(encoding="utf-8")


def test_integrations_module_registers_and_uses_api():
    script = read_script("integrations")
    assert "window.PL.modules.integrations" in script
    assert 'fetch("/api/integrations")' in script
    assert 'fetch("/api/integrations/onebot11",' in script
    assert "renderIntegrationStatus" in script


def test_plugins_module_registers_and_uses_api():
    script = read_script("plugins")
    assert "window.PL.modules.plugins" in script
    assert 'fetch("/api/plugins")' in script
    assert "setPluginEnabled" in script
    assert "renderPluginList" in script


def test_shell_registers_new_module_entries():
    script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert 'integrations: { view: "integrations"' in script
    assert 'plugins: { view: "plugins"' in script
    assert 'create: { view: "create"' in script
    assert 'manage: { view: "manage"' in script
    assert 'test: { view: "test"' in script
    assert 'upload: { view: "personas"' not in script
