"use strict";

const MODULES = {
  chat: { view: "chat", init: window.PL.modules.chat?.init },
  upload: { view: "personas", init: window.PL.modules.upload?.init },
  integrations: { view: "integrations", init: window.PL.modules.integrations?.init },
  plugins: { view: "plugins", init: window.PL.modules.plugins?.init },
  settings: { view: "settings", init: window.PL.modules.settings?.init },
};

function bindShellEvents() {
  $("sidebar-toggle").addEventListener("click", () => setSidebarPinned(!document.body.classList.contains("sidebar-pinned")));
  $("open-guide")?.addEventListener("click", openGuide);
  $("shutdown-project")?.addEventListener("click", requestProjectShutdown);
  $("settings-confirm-cancel").addEventListener("click", () => $("settings-confirm-dialog").close());
  $("settings-confirm-submit").addEventListener("click", confirmSettingsAction);
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
}

function openGuide() {
  if (window.pywebview?.api?.show_launcher) {
    window.pywebview.api.show_launcher();
  } else {
    window.location.href = "/static/onboarding.html";
  }
}

function requestProjectShutdown() {
  state.settingsAction = "shutdown";
  $("settings-confirm-title").textContent = "结束项目";
  $("settings-confirm-detail").textContent = "确定要结束项目吗？将停止本地服务，页面会断开连接。";
  $("settings-confirm-submit").textContent = "确认结束";
  $("shutdown-docker-option").classList.remove("is-hidden");
  $("settings-confirm-dialog").showModal();
}

function setSidebarPinned(pinned) {
  document.body.classList.toggle("sidebar-pinned", pinned);
  $("sidebar-toggle").setAttribute("aria-pressed", String(pinned));
}

async function switchView(view) {
  if (view !== "chat") {
    if (state.audioStarting || state.audioMode !== "idle") cancelAudioActivity();
    closeRealtime();
  }
  const entry = MODULES[view];
  if (!entry) return;
  document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("is-active", button.dataset.view === view));
  const root = $("view-root");
  if (!root) return;
  const response = await fetch(`/static/views/${entry.view}.html`);
  root.innerHTML = await response.text();
  const viewNode = root.firstElementChild;
  if (viewNode) viewNode.classList.remove("is-hidden");
  if (entry.init) entry.init();
  icons();
}

document.addEventListener("DOMContentLoaded", async () => {
  bindShellEvents();
  await switchView("chat");
  await Promise.all([loadStatus(), loadPersonas()]);
  icons();
});
