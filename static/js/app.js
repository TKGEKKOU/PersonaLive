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
  $("refresh-status")?.addEventListener("click", refreshSystemStatus);
  $("collapse-status")?.addEventListener("click", toggleStatusCards);
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
}

function setSidebarPinned(pinned) {
  document.body.classList.toggle("sidebar-pinned", pinned);
  $("sidebar-toggle").setAttribute("aria-pressed", String(pinned));
}

async function switchView(view) {
  if (view !== "chat" && (state.audioStarting || state.audioMode !== "idle")) cancelAudioActivity();
  const entry = MODULES[view];
  if (!entry) return;
  document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("is-active", button.dataset.view === view));
  const root = $("view-root");
  if (!root) return;
  const response = await fetch(`/static/views/${entry.view}.html`);
  root.innerHTML = await response.text();
  if (entry.init) entry.init();
  icons();
}

document.addEventListener("DOMContentLoaded", async () => {
  bindShellEvents();
  await switchView("chat");
  await Promise.all([loadStatus(), loadPersonas()]);
  icons();
});
