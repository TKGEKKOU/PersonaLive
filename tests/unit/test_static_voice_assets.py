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


def test_local_tts_install_controls_are_present():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for control in ["tts-enabled", "tts-use-gpu", "tts-state", "install-tts", "cancel-tts", "remove-tts", "open-tts-directory", "tts-preview-text", "preview-tts", "tts-preview-audio"]:
        assert f'id="{control}"' in html
    for control in ["tts-progress", "tts-progress-detail"]:
        assert f'id="{control}"' in html
    assert 'fetch("/api/tts/status")' in script
    assert 'fetch("/api/tts/install"' in script
    for control in ["edit-tts-enabled", "edit-tts-auto-play", "edit-tts-reference", "edit-tts-reference-status", "edit-tts-preview-reference", "edit-tts-remove-reference"]:
        assert f'id="{control}"' in html
    assert "/reference`" in script
    assert "/reference/audio`" in script


def test_tts_workflows_have_guidance_and_chat_controls():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for control in ["chat-persona-toggle", "chat-persona-menu", "assistant-voice-toggle", "edit-tts-guide", "edit-tts-confirm-upload", "edit-tts-steps", "tts-settings-anchor"]:
        assert f'id="{control}"' in html
    assert "reference/preview" in script
    assert "assistant-voice-toggle" in script
    assert "collectStreamVoice" in script
    assert "voicePlaybackQueue" in script


def test_chat_uses_single_compact_persona_menu():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id="chat-persona-menu"' in html
    assert 'id="chat-persona-toggle"' in html
    assert 'id="persona-sidebar"' not in html
    assert 'id="persona-select"' not in html


def test_home_is_default_and_has_concise_workflow_actions():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert 'id="home-view" class="view home-view"' in html
    assert 'id="upload-view" class="view is-hidden"' in html
    assert 'id="brand-home"' in html
    assert html.count("data-target-view=") == 3
    assert "[data-target-view]" in script


def test_chat_layout_keeps_header_and_composer_visible():
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert ".chat-panel" in styles and "height: 100%" in styles
    assert "grid-template-rows: auto minmax(0,1fr) auto" in styles
    assert ".chat-log" in styles and "min-height: 0" in styles
    assert ".voice-play-button" in styles
