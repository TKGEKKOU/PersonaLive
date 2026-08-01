"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.integrations = { init: initIntegrations };

async function initIntegrations() {
  bindIntegrationEvents();
  await loadIntegrations();
  await fillPersonaOptions();
}

function bindIntegrationEvents() {
  $("onebot-form").addEventListener("submit", saveOnebotConfig);
  $("onebot-group-trigger").addEventListener("change", renderOnebotTrigger);
  $("toggle-onebot-token").addEventListener("click", () => {
    const input = $("onebot-access-token");
    input.type = input.type === "password" ? "text" : "password";
  });
}

function renderOnebotTrigger() {
  $("onebot-prefix-field").classList.toggle("is-hidden", $("onebot-group-trigger").value !== "prefix");
}

function renderIntegrationStatus(cfg) {
  const pill = $("integration-status-pill");
  if (!cfg.enabled) {
    pill.textContent = "未启用";
    pill.className = "status-pill";
    setText("onebot-state", "未启用");
    setText("onebot-status", "启用并保存配置后，NapCat 可连接此地址。");
  } else if (cfg.connected) {
    pill.textContent = "已连接";
    pill.className = "status-pill status-pill-ok";
    setText("onebot-state", "已连接");
    setText("onebot-status", `当前 ${cfg.client_count} 个 OneBot 客户端在线。`);
  } else {
    pill.textContent = "未连接";
    pill.className = "status-pill status-pill-warn";
    setText("onebot-state", "等待连接");
    setText("onebot-status", cfg.error || "NapCat 尚未连接，请检查地址与 Token。");
  }
}

async function loadIntegrations() {
  const data = await api(fetch("/api/integrations"));
  const cfg = data.onebot11 || {};
  $("onebot-enabled").checked = Boolean(cfg.enabled);
  $("onebot-ws-path").value = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${cfg.ws_path || "/api/onebot/ws"}`;
  $("onebot-access-token").value = "";
  $("onebot-group-trigger").value = cfg.group_trigger || "at";
  $("onebot-prefix").value = cfg.prefix || "";
  renderOnebotTrigger();
  renderIntegrationStatus(cfg);
}

async function fillPersonaOptions() {
  const personas = await api(fetch("/api/personas"));
  const select = $("onebot-default-persona");
  select.innerHTML = '<option value="">未设置</option>';
  const current = (await api(fetch("/api/integrations"))).onebot11.default_persona_id || "";
  for (const persona of personas) {
    const option = document.createElement("option");
    option.value = persona.id;
    option.textContent = persona.name;
    select.append(option);
  }
  select.value = current;
}

async function saveOnebotConfig(event) {
  event.preventDefault();
  setText("onebot-save-status");
  const payload = {
    enabled: $("onebot-enabled").checked,
    group_trigger: $("onebot-group-trigger").value,
    prefix: $("onebot-prefix").value.trim(),
    default_persona_id: $("onebot-default-persona").value,
  };
  const token = $("onebot-access-token").value.trim();
  if (token) payload.access_token = token;
  try {
    const saved = await api(fetch("/api/integrations/onebot11", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }));
    renderIntegrationStatus(saved);
    setText("onebot-save-status", "配置已保存" + (saved.enabled ? "，等待客户端连接。" : "，接入已关闭。"));
  } catch (reason) {
    setText("onebot-save-status", reason.message || reason);
  }
}
