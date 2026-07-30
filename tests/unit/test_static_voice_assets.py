from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_frontend_renders_persistent_audio_messages():
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert "appendAudioMessage" in script
    assert "loadConversationMessages" in script
    assert "voice-transcript" in styles
    assert "<audio" not in script


def test_cloud_asr_key_controls_are_removed():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="asr-api-key"' not in html
    assert 'id="asr-base-url"' not in html
    assert 'id="asr-model"' not in html
    assert "ASR_PRESETS" not in script


def test_local_asr_install_controls_are_present():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for control in ["asr-enabled", "asr-python-path", "asr-model-path", "asr-ffmpeg-path", "install-asr", "remove-asr"]:
        assert f'id="{control}"' in html
    assert 'fetch("/api/asr/status")' in script
    assert 'fetch("/api/asr/install"' in script
