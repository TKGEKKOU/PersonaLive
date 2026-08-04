"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.plugins = { init: initPlugins };

async function initPlugins() {
  window.clearInterval(window.__mcpPollTimer);
  await renderPluginList();
  await renderSkillList();
  renderToolOptions(await loadSkillTools());
  $("skill-create-submit").addEventListener("click", createSkill);
  $("skill-upload-btn").addEventListener("click", () => $("skill-upload-input").click());
  $("skill-upload-input").addEventListener("change", (event) =>
    uploadSkillPackage(event.target.files?.[0])
  );
  bindMCPTransport();
  await renderMCPServers();
  await renderMCPTools();
  $("mcp-create-submit").addEventListener("click", createMCPServer);
  window.__mcpPollTimer = window.setInterval(() => {
    renderMCPServers().catch(() => {});
  }, 30000);
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

async function renderSkillList() {
  const list = $("skill-list");
  list.innerHTML = "";
  let skills = [];
  try {
    skills = await api(fetch("/api/skills"));
  } catch (reason) {
    setSkillStatus(reason.message || reason, true);
    return;
  }
  $("skills-count").textContent = `${skills.length} 个技能`;
  if (!skills.length) {
    list.append(empty("还没有技能。在上方新增一个提示词技能，或把 JSON 放入 data/skills/。"));
    return;
  }
  for (const skill of skills) {
    list.append(renderSkillCard(skill));
  }
}

function renderSkillCard(skill) {
  const card = document.createElement("div");
  card.className = "plugin-card";
  const head = document.createElement("div");
  head.className = "plugin-card-head";
  const title = document.createElement("div");
  const name = document.createElement("strong");
  name.textContent = skill.name;
  const meta = document.createElement("span");
  meta.textContent = `${skill.builtin ? "内置" : "自定义"} · ${skill.format === "skillmd" ? "标准包" : "JSON"}`;
  title.append(name, meta);
  head.append(title);
  if (!skill.builtin) {
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "button button-danger";
    remove.textContent = "删除";
    remove.addEventListener("click", () => deleteSkill(skill.name));
    head.append(remove);
  }
  card.append(head);
  if (skill.description) {
    const description = document.createElement("p");
    description.className = "plugin-description";
    description.textContent = skill.description;
    card.append(description);
  }
  if (skill.instructions) {
    const details = document.createElement("details");
    details.className = "plugin-config";
    const summary = document.createElement("summary");
    summary.textContent = "提示词";
    details.append(summary);
    const prompt = document.createElement("pre");
    prompt.className = "skill-prompt";
    prompt.textContent = skill.instructions;
    details.append(prompt);
    card.append(details);
  }
  if (skill.tool_names && skill.tool_names.length) {
    const tools = document.createElement("div");
    tools.className = "skill-tools";
    for (const tool of skill.tool_names) {
      const tag = document.createElement("span");
      tag.className = "skill-tool";
      tag.textContent = tool;
      tools.append(tag);
    }
    card.append(tools);
  }
  return card;
}

async function loadSkillTools() {
  try {
    return await api(fetch("/api/skills/tools"));
  } catch (reason) {
    setSkillStatus(reason.message || reason, true);
    return [];
  }
}

function renderToolOptions(tools) {
  const container = $("skill-tools");
  container.innerHTML = "";
  if (!tools.length) {
    const note = document.createElement("p");
    note.className = "inline-status";
    note.textContent = "没有可附加的工具。";
    container.append(note);
    return;
  }
  for (const tool of tools) {
    const label = document.createElement("label");
    label.className = "toggle-field skill-tool-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = tool.name;
    const span = document.createElement("span");
    span.textContent = tool.requires_confirmation ? `${tool.name}（需确认）` : tool.name;
    label.append(checkbox, span);
    container.append(label);
  }
}

async function createSkill() {
  const name = $("skill-name").value.trim();
  const instructions = $("skill-instructions").value.trim();
  const toolNames = Array.from($("skill-tools").querySelectorAll("input:checked")).map((input) => input.value);
  if (!name || !instructions) {
    setSkillStatus("名称与提示词不能为空。", true);
    return;
  }
  try {
    await api(fetch("/api/skills", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        instructions,
        description: $("skill-description").value.trim(),
        prompt_hint: $("skill-prompt-hint").value.trim(),
        tool_names: toolNames,
      }),
    }));
    $("skill-create-form").open = false;
    $("skill-name").value = "";
    $("skill-description").value = "";
    $("skill-instructions").value = "";
    $("skill-prompt-hint").value = "";
    renderToolOptions(await loadSkillTools());
    await renderSkillList();
    setSkillStatus("技能已保存。", false);
  } catch (reason) {
    setSkillStatus(reason.message || reason, true);
  }
}

async function deleteSkill(name) {
  if (!window.confirm(`删除技能 ${name}？`)) return;
  try {
    await api(fetch(`/api/skills/${encodeURIComponent(name)}`, { method: "DELETE" }));
    await renderSkillList();
  } catch (reason) {
    setSkillStatus(reason.message || reason, true);
  }
}

function setSkillStatus(message, isError) {
  const node = $("skills-status");
  if (!node) return;
  node.textContent = message || "";
  node.classList.toggle("is-error", Boolean(isError));
}

async function uploadSkillPackage(file) {
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  setSkillStatus("正在上传…", false);
  try {
    const result = await api(fetch("/api/skills/upload", { method: "POST", body: form }));
    const parts = [];
    if (result.installed?.length) parts.push(`已安装：${result.installed.join("、")}`);
    if (result.skipped?.length) {
      parts.push(`跳过：${result.skipped.map((item) => `${item.name}（${item.reason}）`).join("；")}`);
    }
    setSkillStatus(parts.join("。") || "上传完成，没有可安装的技能。", Boolean(result.skipped?.length));
    await renderSkillList();
  } catch (reason) {
    setSkillStatus(reason.message || reason, true);
  }
  $("skill-upload-input").value = "";
}

/* ---- MCP 服务器面板 ---- */

const MCP_TRANSPORT_LABELS = {
  stdio: "本地进程",
  streamable_http: "远程 HTTP",
  sse: "远程 SSE",
};

function bindMCPTransport() {
  document.querySelectorAll('input[name="mcp-transport"]').forEach((radio) => {
    radio.addEventListener("change", updateMCPTransportFields);
  });
}

function updateMCPTransportFields() {
  const value = document.querySelector('input[name="mcp-transport"]:checked')?.value || "stdio";
  $("mcp-stdio-fields").hidden = value !== "stdio";
  $("mcp-remote-fields").hidden = value === "stdio";
}

async function renderMCPServers() {
  const list = $("mcp-server-list");
  list.innerHTML = "";
  let servers = [];
  try {
    servers = await api(fetch("/api/mcp/servers"));
  } catch (reason) {
    setMCPStatus(reason.message || reason, true);
    return;
  }
  $("mcp-count").textContent = `${servers.length} 台服务器`;
  if (!servers.length) {
    list.append(empty("还没有配置 MCP 服务器。在上方新增一个服务器，重启应用后其工具会自动注册。"));
    return;
  }
  for (const server of servers) {
    list.append(renderMCPServerCard(server));
  }
}

function renderMCPServerCard(server) {
  const card = document.createElement("div");
  card.className = "plugin-card";
  const head = document.createElement("div");
  head.className = "plugin-card-head";
  const title = document.createElement("div");
  const name = document.createElement("strong");
  name.textContent = server.name;
  const meta = document.createElement("span");
  meta.textContent = `${MCP_TRANSPORT_LABELS[server.transport] || server.transport} · ${server.enabled ? "已启用" : "已停用"}`;
  title.append(name, meta);
  const pill = document.createElement("span");
  pill.className = `status-pill ${mcpStatusPillClass(server.status.status)}`;
  pill.textContent = mcpStatusText(server.status);
  const toggle = document.createElement("label");
  toggle.className = "toggle-field";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = Boolean(server.enabled);
  checkbox.addEventListener("change", () =>
    setMCPServerEnabled(server.name, checkbox.checked)
  );
  const toggleText = document.createElement("span");
  toggleText.textContent = server.enabled ? "启用" : "停用";
  toggle.append(checkbox, toggleText);
  head.append(title, pill, toggle);
  card.append(head);
  if (server.description) {
    const description = document.createElement("p");
    description.className = "plugin-description";
    description.textContent = server.description;
    card.append(description);
  }
  if (server.status.status === "error" && server.status.error) {
    const error = document.createElement("p");
    error.className = "inline-error";
    error.textContent = `连接失败：${server.status.error}`;
    card.append(error);
  }
  const actions = document.createElement("div");
  actions.className = "asr-actions";
  const test = document.createElement("button");
  test.type = "button";
  test.className = "button button-secondary";
  test.textContent = "测试连接";
  test.addEventListener("click", () => testMCPServer(server.name));
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "button button-danger";
  remove.textContent = "删除";
  remove.addEventListener("click", () => deleteMCPServer(server.name));
  actions.append(test, remove);
  card.append(actions);
  return card;
}

function mcpStatusPillClass(status) {
  if (status === "connected") return "status-pill-ok";
  if (status === "error") return "status-pill-err";
  if (status === "disabled") return "status-pill-warn";
  return "";
}

function mcpStatusText(status) {
  if (status.status === "connected") return `${status.tool_count} 个工具`;
  if (status.status === "error") return "连接失败";
  if (status.status === "disabled") return "已停用";
  return "等待重启";
}

async function setMCPServerEnabled(name, enabled) {
  try {
    await api(
      fetch(`/api/mcp/servers/${encodeURIComponent(name)}/${enabled ? "enable" : "disable"}`, {
        method: "POST",
      })
    );
    await renderMCPServers();
  } catch (reason) {
    setMCPStatus(reason.message || reason, true);
    await renderMCPServers();
  }
}

async function testMCPServer(name) {
  setMCPStatus(`正在测试 ${name}…`, false);
  try {
    const result = await api(fetch(`/api/mcp/servers/${encodeURIComponent(name)}/test`, { method: "POST" }));
    if (result.ok) {
      const tools = result.tools.map((tool) => tool.name).join("、") || "（无工具）";
      setMCPStatus(`${name} 连接正常：${result.tool_count} 个工具（${tools}），耗时 ${result.elapsed_ms}ms。`, false);
    } else {
      setMCPStatus(`${name} 连接失败：${result.error}`, true);
    }
  } catch (reason) {
    setMCPStatus(reason.message || reason, true);
  }
}

async function createMCPServer() {
  const name = $("mcp-name").value.trim();
  const transport = document.querySelector('input[name="mcp-transport"]:checked')?.value || "stdio";
  const command = $("mcp-command").value.trim();
  const args = $("mcp-args").value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  const env = parseKeyValueLines($("mcp-env").value);
  const url = $("mcp-url").value.trim();
  const headers = parseKeyValueLines($("mcp-headers").value);
  if (!name) {
    setMCPStatus("服务器名称不能为空。", true);
    return;
  }
  try {
    await api(fetch("/api/mcp/servers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        transport,
        command,
        args,
        env,
        url,
        headers,
        enabled: true,
        description: $("mcp-description").value.trim(),
      }),
    }));
    $("mcp-create-form").open = false;
    resetMCPForm();
    await renderMCPServers();
    setMCPStatus("已保存并连接。", false);
  } catch (reason) {
    setMCPStatus(reason.message || reason, true);
  }
}

function parseKeyValueLines(text) {
  const result = {};
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const eq = trimmed.indexOf("=");
    const colon = trimmed.indexOf(":");
    const sep = eq > 0 && (colon < 0 || eq < colon) ? eq : colon;
    if (sep > 0) {
      result[trimmed.slice(0, sep).trim()] = trimmed.slice(sep + 1).trim();
    }
  }
  return result;
}

function resetMCPForm() {
  $("mcp-name").value = "";
  $("mcp-description").value = "";
  $("mcp-command").value = "";
  $("mcp-args").value = "";
  $("mcp-env").value = "";
  $("mcp-url").value = "";
  $("mcp-headers").value = "";
}

async function deleteMCPServer(name) {
  if (!window.confirm(`删除 MCP 服务器 ${name}？其工具将立即不可用。`)) return;
  try {
    await api(fetch(`/api/mcp/servers/${encodeURIComponent(name)}`, { method: "DELETE" }));
    await renderMCPServers();
    setMCPStatus(`已删除 ${name}。`, false);
  } catch (reason) {
    setMCPStatus(reason.message || reason, true);
  }
}

async function renderMCPTools() {
  const container = $("mcp-tool-list");
  container.innerHTML = "";
  let tools = [];
  try {
    tools = await api(fetch("/api/mcp/tools"));
  } catch (reason) {
    setMCPStatus(reason.message || reason, true);
    return;
  }
  if (!tools.length) {
    const note = document.createElement("p");
    note.className = "inline-status";
    note.textContent = "暂无已注册的 MCP 工具。配置服务器并重启应用后，工具会出现在这里，并可在上方技能中勾选引用。";
    container.append(note);
    return;
  }
  for (const tool of tools) {
    const tag = document.createElement("span");
    tag.className = "skill-tool";
    tag.textContent = tool.requires_confirmation ? `${tool.name}（需确认）` : tool.name;
    tag.title = `${tool.server} · ${tool.description || "无描述"}`;
    container.append(tag);
  }
}

function setMCPStatus(message, isError) {
  const node = $("mcp-status");
  if (!node) return;
  node.textContent = message || "";
  node.classList.toggle("is-error", Boolean(isError));
}
