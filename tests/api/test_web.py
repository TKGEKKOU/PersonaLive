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
        "nav-create",
        "nav-manage",
        "nav-chat",
        "nav-test",
        "nav-settings",
        "nav-integrations",
        "nav-plugins",
        "theme-toggle",
        "view-root",
        "preview-drawer",
        "settings-confirm-dialog",
        "delete-persona-dialog",
        "save-all-dialog",
    ):
        assert f'id="{element_id}"' in response.text

    # 退出确认弹窗由 common.js 动态创建，不再内联在 index.html
    common_js = client.get("/static/js/common.js").text
    assert 'dialog.id = "exit-confirm-dialog"' in common_js

    views = {
        "create": (
            "create-view",
            "generation-mode",
            "batch-form",
            "draft-editor",
            "create-steps",
            "direct-text",
            "upload-button",
        ),
        "manage": (
            "manage-view",
            "manage-persona-list",
            "edit-persona-workspace",
            "edit-files-confirm",
            "edit-live2d-confirm",
            "edit-tts-confirm",
            "save-all-persona",
            "delete-persona",
            "edit-document-list",
        ),
        "test": (
            "test-view",
            "eval-panel",
            "eval-persona",
            "eval-auto-run",
            "eval-state-pill",
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
            "embedding-device",
            "embedding-state",
            "embedding-progress",
            "install-embedding",
            "remove-embedding",
            "open-embedding-directory",
            "chunk-size",
            "chunk-overlap",
            "web-search-provider",
            "web-search-api-key",
            "web-search-base-url",
            "web-search-guide",
            "open-asr-directory",
            "open-gptsovits-directory",
            "install-gptsovits",
            "gptsovits-preset",
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
            "skill-list",
            "mcp-server-list",
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
            'fetch("/api/eval/run"',
            'fetch("/api/eval/status")',
            'fetch("/api/eval/results")',
        ),
        "/static/js/chat.js": (
            'fetch(`/api/personas/${state.activePersona.id}/agent/resume`',
            'agent.stage',
            '/agent/stream',
        ),
        "/static/js/settings.js": (
            'fetch("/api/settings"',
            'method: "DELETE"',
            '确认重置配置',
        ),
        "/static/js/plugins.js": (
            'encodeURIComponent(name)}/grants',
            'category',
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
    assert "text-embedding-v4" not in client.get("/static/js/common.js").text
    assert "Qwen3-Embedding-0.6B" in client.get("/static/js/settings.js").text
    assert "请输入 API Key" in client.get("/static/js/settings.js").text
    assert "已保存，可输入新 Key 替换" in client.get("/static/js/settings.js").text
    assert "已配置，留空保持" not in client.get("/static/js/settings.js").text
    assert "保存前确认" in client.get("/static/index.html").text
    assert "永久删除，无法恢复" in client.get("/static/index.html").text
