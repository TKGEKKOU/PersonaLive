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
  $("toggle-onebot-token").addEventListener("click", toggleSecret("onebot-access-token"));
  $("qq-official-form").addEventListener("submit", saveQqOfficialConfig);
  $("qq-official-group-trigger").addEventListener("change", renderQqOfficialTrigger);
  $("toggle-qq-official-secret").addEventListener("click", toggleSecret("qq-official-secret"));
}

function toggleSecret(id) {
  return () => {
    const input = $(id);
    input.type = input.type === "password" ? "text" : "password";
  };
}

function renderOnebotTrigger() {
  $("onebot-prefix-field").classList.toggle("is-hidden", $("onebot-group-trigger").value !== "prefix");
}

function renderQqOfficialTrigger() {
  $("qq-official-prefix-field").classList.toggle("is-hidden", $("qq-official-group-trigger").value !== "prefix");
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

function renderQqOfficialStatus(cfg) {
  const pill = $("qq-official-status-pill");
  const notice = $("qq-official-notice");
  const enabled = Boolean(cfg.enabled);
  const configured = Boolean(cfg.appid) && Boolean(cfg.secret_configured);
  if (!enabled) {
    pill.textContent = "未启用";
    pill.className = "status-pill";
    setText("qq-official-state", "未启用");
    setText("qq-official-status", "填写 AppID/AppSecret 并启用后，通过官方 WebSocket 网关直连 QQ。");
    notice.hidden = true;
  } else if (cfg.connected) {
    pill.textContent = "已连接";
    pill.className = "status-pill status-pill-ok";
    setText("qq-official-state", "已连接");
    const env = cfg.sandbox ? "沙箱环境" : "正式环境";
    setText("qq-official-status", `已连上${env}，机器人 OpenID：${cfg.bot_openid || "未知"}。`);
    notice.hidden = true;
  } else if (!configured) {
    pill.textContent = "待配置";
    pill.className = "status-pill status-pill-warn";
    setText("qq-official-state", "缺少凭据");
    setText("qq-official-status", "请填写开放平台的 AppID 与 AppSecret。");
    notice.hidden = false;
  } else {
    pill.textContent = cfg.error ? "未连接" : "连接中";
    pill.className = "status-pill status-pill-warn";
    setText("qq-official-state", cfg.error ? "未连接" : "连接中");
    setText("qq-official-status", cfg.error || "正在连接官方网关，请稍候……");
    notice.hidden = false;
  }
  if (!notice.hidden) {
    const state = $("qq-official-state").textContent;
    setText("qq-official-notice-title", state === "连接中" ? "正在连接官方网关" : "官方通道当前未连接");
  }
}

async function loadIntegrations() {
  const data = await api(fetch("/api/integrations"));
  renderOnebot(data.onebot11 || {});
  renderQqOfficial(data.qq_official || {});
}

function renderOnebot(cfg) {
  $("onebot-enabled").checked = Boolean(cfg.enabled);
  $("onebot-ws-path").value = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${cfg.ws_path || "/api/onebot/ws"}`;
  $("onebot-access-token").value = "";
  $("onebot-group-trigger").value = cfg.group_trigger || "at";
  $("onebot-prefix").value = cfg.prefix || "";
  renderOnebotTrigger();
  renderIntegrationStatus(cfg);
}

function renderQqOfficial(cfg) {
  $("qq-official-enabled").checked = Boolean(cfg.enabled);
  $("qq-official-appid").value = cfg.appid || "";
  $("qq-official-secret").value = "";
  $("qq-official-sandbox").checked = cfg.sandbox !== false;
  $("qq-official-group-trigger").value = cfg.group_trigger || "at";
  $("qq-official-prefix").value = cfg.prefix || "";
  renderQqOfficialTrigger();
  renderQqOfficialStatus(cfg);
}

async function fillPersonaOptions() {
  const personas = await api(fetch("/api/personas"));
  const data = await api(fetch("/api/integrations"));
  const current = {
    onebot: data.onebot11?.default_persona_id || "",
    qq_official: data.qq_official?.default_persona_id || "",
  };
  for (const [key, selectId] of [["onebot", "onebot-default-persona"], ["qq_official", "qq-official-default-persona"]]) {
    const select = $(selectId);
    select.innerHTML = '<option value="">未设置</option>';
    for (const persona of personas) {
      const option = document.createElement("option");
      option.value = persona.id;
      option.textContent = persona.name;
      select.append(option);
    }
    select.value = current[key];
  }
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
    setText("onebot-save-status", reason.message || reason, true, true);
  }
}

async function saveQqOfficialConfig(event) {
  event.preventDefault();
  setText("qq-official-save-status");
  const payload = {
    enabled: $("qq-official-enabled").checked,
    appid: $("qq-official-appid").value.trim(),
    sandbox: $("qq-official-sandbox").checked,
    group_trigger: $("qq-official-group-trigger").value,
    prefix: $("qq-official-prefix").value.trim(),
    default_persona_id: $("qq-official-default-persona").value,
  };
  const secret = $("qq-official-secret").value.trim();
  if (secret) payload.secret = secret;
  try {
    const saved = await api(fetch("/api/integrations/qq_official", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }));
    renderQqOfficialStatus(saved);
    setText("qq-official-save-status", "配置已保存" + (saved.enabled ? "，正在连接官方网关。" : "，接入已关闭。"));
  } catch (reason) {
    setText("qq-official-save-status", reason.message || reason, true, true);
  }
}
