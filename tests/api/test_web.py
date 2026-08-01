def test_root_redirects_to_web_workbench(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/static/index.html"


def test_web_workbench_exposes_shell_and_module_views(client):
    response = client.get("/static/index.html")
    assert response.status_code == 200
    assert 'href="/static/styles.css"' in response.text
    assert 'src="/static/js/app.js"' in response.text
    for element_id in (
        "nav-upload",
        "nav-chat",
        "nav-settings",
        "nav-integrations",
        "nav-plugins",
        "theme-toggle",
        "exit-confirm-dialog",
        "view-root",
        "preview-drawer",
        "settings-confirm-dialog",
        "delete-persona-dialog",
    ):
        assert f'id="{element_id}"' in response.text

    views = {
        "personas": (
            "upload-view",
            "material-action",
            "edit-persona-select",
            "edit-persona-form",
            "generation-mode",
            "batch-form",
            "draft-editor",
            "delete-persona",
        ),
        "chat": (
            "chat-view",
            "chat-persona-menu",
            "chat-log",
            "question-form",
            "confirmation-panel",
            "chat-persona-toggle",
        ),
        "settings": (
            "settings-view",
            "settings-system-status",
            "settings-open-milvus",
            "settings-form",
            "reset-settings",
            "llm-provider",
            "openai-api-key",
            "embedding-provider",
            "embedding-api-key",
            "managed-embedding-preset",
            "embedding-model-source",
            "embedding-device",
            "embedding-dimensions",
            "embedding-dimension-warning",
            "web-search-provider",
            "web-search-api-key",
            "web-search-base-url",
            "web-search-guide",
            "open-asr-directory",
            "open-tts-directory",
            "docker-exit-policy",
            "docker-save-exit",
            "docker-pause-now",
            "docker-remove-now",
        ),
        "integrations": (
            "integrations-view",
            "onebot-enabled",
            "onebot-ws-path",
            "onebot-access-token",
            "onebot-group-trigger",
            "onebot-default-persona",
            "save-onebot",
        ),
        "plugins": (
            "plugins-view",
            "plugin-list",
            "plugins-count",
        ),
    }
    for name, ids in views.items():
        body = client.get(f"/static/views/{name}.html")
        assert body.status_code == 200
        for element_id in ids:
            assert f'id="{element_id}"' in body.text

    scripts = {
        "/static/js/app.js": (
            'fetch(`/static/views/${entry.view}.html`)',
            'switchView("chat")',
        ),
        "/static/js/personas.js": (
            'fetch("/api/persona-drafts/upload"',
            'fetch(`/api/persona-drafts/${state.draft.id}`',
            'fetch(`/api/persona-drafts/${state.draft.id}/confirm`',
            'fetch(`/api/personas/${persona.id}`, { method: "DELETE" })',
        ),
        "/static/js/chat.js": (
            'fetch(`/api/personas/${state.activePersona.id}/agent/query`',
            'fetch(`/api/personas/${state.activePersona.id}/agent/resume`',
        ),
        "/static/js/settings.js": (
            'fetch("/api/settings"',
            'method: "DELETE"',
            '确认重置配置',
            'fetch("/api/system/docker-settings"',
            'fetch("/api/system/docker/pause", { method: "POST" })',
            'fetch("/api/system/docker/remove", { method: "POST" })',
        ),
    }
    for path, contracts in scripts.items():
        script = client.get(path)
        assert script.status_code == 200
        for contract in contracts:
            assert contract in script.text

    assert "...(state.draft.profile || {})" in client.get("/static/js/personas.js").text
    assert "https://api.deepseek.com" in client.get("/static/js/common.js").text
    assert "https://dashscope.aliyuncs.com/compatible-mode/v1" in client.get("/static/js/common.js").text
    assert "text-embedding-v4" in client.get("/static/js/common.js").text
    assert "获取 Key 与填写示例" in client.get("/static/views/settings.html").text
    assert "请输入 API Key" in client.get("/static/js/settings.js").text
    assert "已保存，可输入新 Key 替换" in client.get("/static/js/settings.js").text
    assert "已配置，留空保持" not in client.get("/static/js/settings.js").text
    assert "保存前确认" in client.get("/static/index.html").text
    assert "永久删除，无法恢复" in client.get("/static/index.html").text
