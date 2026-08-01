import json

from integrations.bindings import (
    bind_persona,
    load_bindings,
    persona_for,
    save_bindings,
)


def test_bind_and_resolve(tmp_path):
    path = tmp_path / "bindings.json"
    bindings = load_bindings(path)
    bind_persona(bindings, "private", "20001", "p1")
    bind_persona(bindings, "group", "30001", "p2")
    save_bindings(path, bindings)
    assert persona_for(load_bindings(path), "private", "20001", "default-p") == "p1"
    assert persona_for(load_bindings(path), "group", "30001", "default-p") == "p2"
    assert persona_for(load_bindings(path), "private", "99999", "default-p") == "default-p"
    assert persona_for(load_bindings(path), "private", "99999", "") is None


def test_bindings_survive_file_roundtrip(tmp_path):
    path = tmp_path / "bindings.json"
    bindings = load_bindings(path)
    bind_persona(bindings, "group", "30001", "p2")
    save_bindings(path, bindings)
    assert json.loads(path.read_text(encoding="utf-8"))["group"]["30001"] == "p2"
