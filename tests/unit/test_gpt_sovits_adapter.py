from pathlib import Path

from voice.gpt_sovits.adapter import GPTSoVITSAdapter
from voice.gpt_sovits.config import GPTSoVITSConfig


def _adapter(tmp_path: Path) -> GPTSoVITSAdapter:
    return GPTSoVITSAdapter(GPTSoVITSConfig(tmp_path), tmp_path)


def test_listener_pids_parses_netstat(monkeypatch):
    adapter = _adapter(Path("."))

    class FakeOutput:
        stdout = (
            "  TCP    127.0.0.1:17005    0.0.0.0:0    LISTENING    12345\n"
            "  TCP    0.0.0.0:17005      0.0.0.0:0    LISTENING    9999\n"
            "  TCP    127.0.0.1:17000    0.0.0.0:0    LISTENING    7777\n"
        )

    monkeypatch.setattr(
        "voice.gpt_sovits.adapter.subprocess.run",
        lambda *args, **kwargs: FakeOutput(),
    )

    assert adapter._listener_pids(17005) == [12345, 9999]


def test_stop_service_kills_untracked_listener(monkeypatch):
    adapter = _adapter(Path("."))
    adapter._process = None
    calls = []
    monkeypatch.setattr(adapter, "_listener_pids", lambda port: [12345])
    monkeypatch.setattr(
        "voice.gpt_sovits.adapter.subprocess.run",
        lambda args, **kwargs: calls.append(args) or type("R", (), {})(),
    )

    adapter.stop_service()

    assert any(str(args).find("12345") >= 0 for args in calls)


def test_is_alive_checks_port_when_process_untracked(monkeypatch):
    adapter = _adapter(Path("."))
    adapter._process = None

    monkeypatch.setattr(
        "voice.gpt_sovits.adapter.socket.create_connection",
        lambda *args, **kwargs: type("Sock", (), {"__enter__": lambda s: s, "__exit__": lambda *a: None})(),
    )
    assert adapter.is_alive() is True

    monkeypatch.setattr(
        "voice.gpt_sovits.adapter.socket.create_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("refused")),
    )
    assert adapter.is_alive() is False
