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
  $("settings-confirm-cancel").addEventListener("click", () => $("settings-confirm-dialog").close());
  $("settings-confirm-submit").addEventListener("click", confirmSettingsAction);
  $("exit-confirm-cancel").addEventListener("click", () => $("exit-confirm-dialog").close());
  $("exit-confirm-submit").addEventListener("click", () => {
    const btn = $("exit-confirm-submit");
    btn.classList.add("is-loading");
    btn.disabled = true;
    const label = $("exit-confirm-label");
    if (label) label.textContent = "正在退出…";
    if (window.pywebview?.api?.do_exit) window.pywebview.api.do_exit();
  });
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
}

function setSidebarPinned(pinned) {
  document.body.classList.toggle("sidebar-pinned", pinned);
  $("sidebar-toggle").setAttribute("aria-pressed", String(pinned));
}

async function switchView(view) {
  if (view !== "chat") {
    if (state.voiceActive) stopVoiceChat();
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
  await Promise.all([loadStatus(), loadPersonas(), loadAsrStatus(), loadTtsStatus()]);
  if (location.hash === "#docker-exit") {
    await switchView("settings");
    const anchor = $("docker-exit-anchor");
    if (anchor) {
      anchor.open = true;
      anchor.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }
  icons();
});
