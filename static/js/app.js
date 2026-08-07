"use strict";

const MODULES = {
  chat: { view: "chat", init: window.PL.modules.chat?.init },
  create: { view: "create", init: window.PL.modules.create?.init },
  manage: { view: "manage", init: window.PL.modules.manage?.init, onShow: window.PL.modules.manage?.onShow },
  voice: { view: "voice", init: window.PL.modules.voice?.init },
  integrations: { view: "integrations", init: window.PL.modules.integrations?.init },
  plugins: { view: "plugins", init: window.PL.modules.plugins?.init },
  test: { view: "test", init: window.PL.modules.test?.init },
  settings: { view: "settings", init: window.PL.modules.settings?.init },
};

/* 视图缓存：页面只加载并初始化一次，之后切换仅显隐切换，保留表单、
   评测结果等页面状态，避免每次切页都重建 DOM 导致状态丢失。 */
const VIEW_NODES = {};

/* 音频解锁：浏览器自动播放策略下，AudioContext 可能处于 suspended 状态，
   经它路由的 <audio> 会静音（元素在"播放"但无声）。在首次用户交互时创建
   并 resume 一个共享 AudioContext、播放一段静音缓冲，为整个会话解锁音频。
   同时暴露 PL.unlockAudio 供各模块在播放前主动调用。 */
(function unlockAudioOnGesture() {
  let unlocked = false;
  const unlock = () => {
    if (unlocked) return;
    unlocked = true;
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const buffer = ctx.createBuffer(1, 1, 22050);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      source.start(0);
      if (ctx.state === "suspended") ctx.resume().catch(() => {});
    } catch (e) { /* ignore */ }
  };
  ["pointerdown", "keydown", "touchstart", "click"].forEach((type) => {
    window.addEventListener(type, unlock, { capture: true, passive: true });
  });
  window.PL = window.PL || {};
  window.PL.unlockAudio = unlock;
})();

/* 全局单音频：任意 <audio> 开始播放时，暂停页面上其他所有正在播放的音频，
   保证同一时间只有一条声音（聊天流式、预览、试听、播放按钮共用此规则）。 */
(function enforceSingleAudio() {
  document.addEventListener("play", (event) => {
    const el = event.target;
    if (!el || el.tagName !== "AUDIO") return;
    document.querySelectorAll("audio").forEach((other) => {
      if (other !== el && !other.paused && !other.ended) {
        try { other.pause(); } catch (e) { /* ignore */ }
      }
    });
  }, true);

  window.PL = window.PL || {};
  window.PL.audio = {
    host: null,
    hostEl() {
      if (!this.host) {
        this.host = document.createElement("div");
        this.host.id = "audio-single-host";
        this.host.hidden = true;
        document.body.append(this.host);
      }
      return this.host;
    },
    /* 播放并确保元素挂进 DOM：事件冒泡到 document（单音频策略 + Live2D 口型依赖）。 */
    play(el) {
      if (el && !el.isConnected) this.hostEl().append(el);
      return el.play();
    },
  };
})();

function bindShellEvents() {
  $("sidebar-toggle").addEventListener("click", () => setSidebarPinned(!document.body.classList.contains("sidebar-pinned")));
  $("settings-confirm-cancel").addEventListener("click", () => $("settings-confirm-dialog").close());
  $("settings-confirm-submit").addEventListener("click", confirmSettingsAction);
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
  let node = VIEW_NODES[view];
  if (!node) {
    const response = await fetch(`/static/views/${entry.view}.html`);
    const template = document.createElement("template");
    template.innerHTML = await response.text();
    node = template.content.firstElementChild;
    if (!node) return;
    VIEW_NODES[view] = node;
    root.append(node);
    if (entry.init) entry.init();
  }
  Object.entries(VIEW_NODES).forEach(([key, viewNode]) => {
    viewNode.classList.toggle("is-hidden", key !== view);
  });
  if (entry.onShow) entry.onShow();
  icons();
}

document.addEventListener("DOMContentLoaded", async () => {
  bindShellEvents();
  await switchView("chat");
  await Promise.all([loadStatus(), loadPersonas(), loadAsrStatus(), loadTtsStatus()]);
  if (location.hash === "#plugins") {
    await switchView("plugins");
  }
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
