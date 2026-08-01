"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.plugins = { init: initPlugins };

async function initPlugins() {
  await renderPluginList();
}

async function renderPluginList() {
  const list = $("plugin-list");
  list.innerHTML = "";
  let plugins = [];
  try {
    plugins = await api(fetch("/api/plugins"));
  } catch (reason) {
    setText("plugins-status", reason.message || reason);
    return;
  }
  $("plugins-count").textContent = `${plugins.length} 个插件`;
  if (!plugins.length) {
    list.append(empty("还没有插件。在项目 plugins/ 目录放入带 plugin.json 的插件后重启应用。"));
    return;
  }
  for (const plugin of plugins) {
    list.append(renderPluginCard(plugin));
  }
}

function renderPluginCard(plugin) {
  const card = document.createElement("div");
  card.className = "plugin-card";
  card.dataset.plugin = plugin.name;
  const head = document.createElement("div");
  head.className = "plugin-card-head";
  const title = document.createElement("div");
  const name = document.createElement("strong");
  name.textContent = plugin.name;
  const meta = document.createElement("span");
  meta.textContent = `v${plugin.version}${plugin.author ? ` · ${plugin.author}` : ""}`;
  title.append(name, meta);
  const toggle = document.createElement("label");
  toggle.className = "toggle-field";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = Boolean(plugin.enabled);
  checkbox.addEventListener("change", () => setPluginEnabled(plugin.name, checkbox.checked));
  const toggleText = document.createElement("span");
  toggleText.textContent = plugin.enabled ? "启用" : "禁用";
  toggle.append(checkbox, toggleText);
  head.append(title, toggle);
  card.append(head);
  if (plugin.description) {
    const description = document.createElement("p");
    description.className = "plugin-description";
    description.textContent = plugin.description;
    card.append(description);
  }
  if (plugin.error) {
    const error = document.createElement("p");
    error.className = "inline-error";
    error.textContent = plugin.error;
    card.append(error);
  } else {
    card.append(renderPluginConfig(plugin));
  }
  return card;
}

function renderPluginConfig(plugin) {
  const details = document.createElement("details");
  details.className = "plugin-config";
  const summary = document.createElement("summary");
  summary.textContent = "配置";
  details.append(summary);
  const config = plugin.config || {};
  const keys = Object.keys(config);
  if (!keys.length) {
    const note = document.createElement("p");
    note.className = "inline-status";
    note.textContent = "该插件没有可配置项。";
    details.append(note);
    return details;
  }
  const form = document.createElement("div");
  form.className = "settings-grid settings-grid-four";
  const inputs = {};
  for (const key of keys) {
    const label = document.createElement("label");
    label.className = "field";
    const span = document.createElement("span");
    span.textContent = key;
    label.append(span);
    const value = config[key];
    if (typeof value === "boolean") {
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = value;
      inputs[key] = input;
      label.append(input);
    } else {
      const input = document.createElement("input");
      input.value = value === null || value === undefined ? "" : String(value);
      inputs[key] = input;
      label.append(input);
    }
    form.append(label);
  }
  details.append(form);
  const actions = document.createElement("div");
  actions.className = "asr-actions";
  const save = document.createElement("button");
  save.type = "button";
  save.className = "button button-secondary";
  save.textContent = "保存配置";
  save.addEventListener("click", async () => {
    const updates = {};
    for (const [key, input] of Object.entries(inputs)) {
      updates[key] = input.type === "checkbox" ? input.checked : input.value;
    }
    try {
      await api(fetch(`/api/plugins/${plugin.name}/config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ config: updates }),
      }));
      await renderPluginList();
    } catch (reason) {
      setText("plugins-status", reason.message || reason);
    }
  });
  actions.append(save);
  details.append(actions);
  return details;
}

async function setPluginEnabled(name, enabled) {
  try {
    await api(fetch(`/api/plugins/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }));
    await renderPluginList();
  } catch (reason) {
    setText("plugins-status", reason.message || reason);
    await renderPluginList();
  }
}
