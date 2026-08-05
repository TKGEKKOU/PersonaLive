from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read_view(name: str) -> str:
    return (ROOT / "static" / "views" / f"{name}.html").read_text(encoding="utf-8")


def read_script(name: str) -> str:
    return (ROOT / "static" / "js" / f"{name}.js").read_text(encoding="utf-8")


def test_frontend_renders_persistent_audio_messages():
    script = read_script("chat")
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert "appendAudioMessage" in script
    assert "loadConversationMessages" in script
    assert "voice-transcript" in styles
    assert "<audio" not in script


def test_cloud_asr_key_controls_are_removed():
    html = read_view("settings")
    script = read_script("settings")
    assert 'id="asr-api-key"' not in html
    assert 'id="asr-base-url"' not in html
    assert 'id="asr-model"' not in html
    assert "ASR_PRESETS" not in script


def test_local_asr_install_controls_are_present():
    html = read_view("settings")
    script = read_script("settings")
    for control in ["asr-enabled", "asr-python-path", "asr-model-path", "asr-ffmpeg-path", "install-asr", "remove-asr"]:
        assert f'id="{control}"' in html
    assert 'fetch("/api/asr/status")' in script
    assert 'fetch("/api/asr/install"' in script


def test_local_tts_install_controls_are_present():
    html = read_view("settings") + read_view("manage")
    script = read_script("settings") + read_script("personas")
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
    html = read_view("chat") + read_view("create") + read_view("manage") + read_view("test") + read_view("settings")
    script = read_script("chat") + read_script("personas")
    for control in [
        "chat-persona-toggle",
        "chat-persona-menu",
        "assistant-voice-toggle",
        "edit-tts-confirm",
        "edit-tts-drop",
        "edit-tts-message",
        "edit-tts-open-settings",
        "edit-tts-preview-text",
        "tts-settings-anchor",
    ]:
        assert f'id="{control}"' in html
    assert "reference/preview" in script
    assert "assistant-voice-toggle" in script
    assert "collectStreamVoice" in script
    assert "voicePlaybackQueue" in script


def test_chat_uses_single_compact_persona_menu():
    html = read_view("chat")
    assert 'id="chat-persona-menu"' in html
    assert 'id="chat-persona-toggle"' in html
    assert 'id="persona-sidebar"' not in html
    assert 'id="persona-select"' not in html


def test_chat_is_default_and_home_guidance_is_removed():
    html = read_view("chat") + read_view("create") + read_view("manage") + read_view("test") + read_view("settings")
    script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert 'id="home-view"' not in html
    assert 'id="create-view" class="view is-hidden"' in read_view("create")
    assert 'id="manage-view" class="view is-hidden"' in read_view("manage")
    assert 'id="test-view" class="view is-hidden"' in read_view("test")
    assert 'id="brand-home"' not in html
    assert 'switchView("chat")' in script
    assert 'id="settings-system-status"' in read_view("settings")
    assert 'id="settings-open-milvus"' in read_view("settings")
    assert 'id="system-status"' not in html


def test_settings_service_status_covers_required_local_dependencies():
    html = read_view("settings")
    script = read_script("common") + read_script("settings")
    for service in ["sqlite", "milvus", "embedding", "asr", "tts"]:
        assert f'data-service-status="{service}"' in html
    assert 'fetch("/api/status")' in script
    assert 'fetch("/api/asr/status")' in script
    assert 'fetch("/api/tts/status")' in script
    assert "renderServiceStatus" in script


def test_managed_embedding_controls_and_model_sources_are_present():
    html = read_view("settings")
    script = read_script("settings")
    for control in ["embedding-provider", "managed-embedding-preset", "embedding-model-source", "embedding-device", "embedding-state", "embedding-progress", "install-embedding", "cancel-embedding", "remove-embedding", "open-embedding-directory"]:
        assert f'id="{control}"' in html
    assert "Qwen/Qwen3-Embedding-0.6B" in html
    assert "ModelScope" in html
    for endpoint in ["/api/embedding/status", "/api/embedding/install", "/api/embedding/model-directory"]:
        assert endpoint in script


def test_api_key_fields_support_reveal_and_copy():
    html = read_view("settings")
    script = read_script("settings")
    for field in ["openai-api-key", "embedding-api-key", "web-search-api-key"]:
        assert f'id="toggle-{field}"' in html
        assert f'id="copy-{field}"' in html
    assert "/api/settings/reveal-key" in script
    assert "toggleApiKeyVisibility" in script
    assert "copyApiKey" in script


def test_resource_install_buttons_explain_ready_and_installing_states():
    script = read_script("settings")
    assert 'textContent = "已安装"' in script
    assert 'textContent = "安装中"' in script
    assert 'textContent = "下载并启用"' in script
    assert "markEmbeddingSelectionChanged" in script


def test_primary_navigation_uses_collapsible_sidebar_with_chat_first():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert html.index('id="nav-chat"') < html.index('id="nav-create"') < html.index('id="nav-manage"') < html.index('id="nav-test"') < html.index('id="nav-settings"')
    assert 'id="sidebar-toggle"' in html
    assert "setSidebarPinned" in script
    assert "personalive:sidebar-collapsed" not in script
    assert ".site-sidebar" in styles
    assert ".site-sidebar:hover" in styles
    assert ".primary-nav" in styles
    assert ".nav-item.is-active" in styles
    assert "body.sidebar-pinned" in styles


def test_settings_are_rendered_as_one_continuous_page():
    html = read_view("settings")
    script = read_script("settings")
    assert 'class="settings-nav"' not in html
    assert "data-settings-target" not in html
    assert "switchSettingsPanel" not in script
    assert "prepareSettingsSections" in script


def test_pages_drop_decorative_section_labels_and_repeated_intros():
    html = read_view("chat") + read_view("create") + read_view("manage") + read_view("test") + read_view("settings")
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    for label in ["section-index", "panel-index", "01 / MATERIAL", "03 / SETTINGS", "CURRENT PERSONA"]:
        assert label not in html
    assert ".material-toolbar" not in styles
    assert ".section-index" not in styles


def test_chat_layout_keeps_header_and_composer_visible():
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert ".chat-panel" in styles and "height: 100%" in styles
    assert "grid-template-rows: auto minmax(0,1fr) auto" in styles
    assert ".chat-log" in styles and "min-height: 0" in styles
    assert ".voice-play-button" in styles


def test_streaming_voice_is_synthesized_once_after_final_text():
    source = read_script("chat")

    assert "flushStreamVoice(false" not in source
    assert "flushStreamVoice(true" in source


def test_chat_process_is_inside_container_and_loading_state_exists():
    html = read_view("chat")
    script = read_script("chat")
    styles = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
    assert 'id="chat-process-panel"' in html
    assert 'id="chat-process-toggle"' in html
    assert "showReplyLoading" in script
    assert "renderChatProcess" in script
    assert "appendResultDetails(node, result)" in script
    assert "loading-bubble" in styles
    assert "background: transparent" in styles
    assert ".chat-panel" in styles and "border: 0" in styles
    assert "right: clamp(16px,4vw,48px)" in styles
    assert "nodeLabels" in script
