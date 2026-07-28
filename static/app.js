"use strict";

const state = {
  draft: null,
  personas: [],
  activePersona: null,
  editPersona: null,
  conversationId: crypto.randomUUID(),
  poller: null,
  editPoller: null,
  pendingAction: null,
  settingsAction: null,
  deletePersona: null,
  savedEmbeddingDimensions: 512,
};
const $ = (id) => document.getElementById(id);
const LLM_PRESETS = {
  openai: { baseUrl: "https://api.openai.com/v1", model: "gpt-5.6-sol" },
  deepseek: { baseUrl: "https://api.deepseek.com", model: "deepseek-v4-flash" },
  qwen: { baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
};
const EMBEDDING_PRESETS = {
  qwen: { baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "text-embedding-v4", dimensions: 512, sendDimensions: true },
};
const WEB_SEARCH_GUIDES = {
  off: { text: "选择服务并填写 API Key 后，联网搜索才会启用。" },
  tavily: { text: "只需填写 Tavily API Key。适合通用英文与多语种网页搜索。", label: "官方入口", href: "https://app.tavily.com/", link: "Tavily" },
  bocha: { text: "只需填写博查 API Key。接口会返回适合 RAG 使用的网页摘要。", label: "官方入口", href: "https://open.bocha.cn/", link: "博查 AI" },
  custom: { text: "填写完整的 Web Search 接口地址和 API Key。接口需兼容博查/Bing 结果格式：POST 请求、Bearer 鉴权，并返回 data.webPages.value。" },
};

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await Promise.all([loadStatus(), loadPersonas(), loadSettings()]);
  icons();
});

function icons() { if (window.lucide) window.lucide.createIcons(); }
async function api(request) {
  const response = await request;
  const data = response.headers.get("content-type")?.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `请求失败 (${response.status})`);
  return data;
}
function setText(id, value = "") { $(id).textContent = value?.message || value; }

function bindEvents() {
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  document.querySelectorAll('input[name="material-action"]').forEach((input) => input.addEventListener("change", () => switchMaterialMode(input.value)));
  $("document-files").addEventListener("change", () => summarizeFiles("document-files", "file-summary", "未选择文件"));
  $("edit-document-files").addEventListener("change", () => summarizeFiles("edit-document-files", "edit-file-summary", "添加文件或图片"));
  $("batch-form").addEventListener("submit", uploadDraft);
  $("new-batch").addEventListener("click", resetDraft);
  $("save-draft").addEventListener("click", saveDraft);
  $("confirm-draft").addEventListener("click", confirmDraft);
  $("edit-persona-select").addEventListener("change", loadEditPersona);
  $("edit-persona-form").addEventListener("submit", saveEditPersona);
  $("edit-upload-form").addEventListener("submit", uploadEditDocuments);
  $("persona-select").addEventListener("change", selectPersona);
  $("question-form").addEventListener("submit", submitQuestion);
  $("confirm-action").addEventListener("click", () => resumeAgent(true));
  $("cancel-action").addEventListener("click", () => resumeAgent(false));
  $("question").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("question-form").requestSubmit(); } });
  $("settings-form").addEventListener("submit", requestSettingsSave);
  $("reset-settings").addEventListener("click", requestSettingsReset);
  $("settings-confirm-cancel").addEventListener("click", () => $("settings-confirm-dialog").close());
  $("settings-confirm-submit").addEventListener("click", confirmSettingsAction);
  $("delete-persona").addEventListener("click", requestPersonaDeletion);
  $("delete-persona-cancel").addEventListener("click", () => $("delete-persona-dialog").close());
  $("delete-persona-confirm").addEventListener("click", confirmPersonaDeletion);
  $("llm-provider").addEventListener("change", applyLlmPreset);
  $("embedding-provider").addEventListener("change", applyEmbeddingPreset);
  $("embedding-dimensions").addEventListener("input", renderEmbeddingWarning);
  $("web-search-provider").addEventListener("change", renderWebSearchSettings);
  $("close-preview").addEventListener("click", closePreview);
  $("preview-backdrop").addEventListener("click", closePreview);
}

function switchView(view) {
  for (const name of ["upload", "chat", "settings"]) $(name + "-view").classList.toggle("is-hidden", name !== view);
  document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("is-active", button.dataset.view === view));
}

function switchMaterialMode(mode) {
  $("create-material-panel").classList.toggle("is-hidden", mode !== "create");
  $("edit-material-panel").classList.toggle("is-hidden", mode !== "edit");
  $("new-batch").classList.toggle("is-hidden", mode !== "create");
}

async function loadStatus() {
  try {
    const status = await api(fetch("/api/status"));
    $("system-status").replaceChildren(statusBadge("MySQL", status.mysql), statusBadge("Milvus", status.milvus));
  } catch { $("system-status").textContent = "离线"; }
}
function statusBadge(label, value) {
  const node = document.createElement("span");
  node.className = `status-badge${value === "ok" ? " is-ok" : ""}`;
  node.textContent = `${label} ${value}`;
  return node;
}

function summarizeFiles(inputId, outputId, emptyText) {
  const files = [...$(inputId).files];
  $(outputId).textContent = files.length ? `${files.length} 个 · ${files.map((file) => file.name).join("、")}` : emptyText;
}

async function uploadDraft(event) {
  event.preventDefault();
  const files = [...$("document-files").files];
  const text = $("direct-text").value.trim();
  if (!files.length && !text) return setText("upload-error", "请选择资料或输入文本");
  const form = new FormData();
  form.append("mode", document.querySelector('input[name="mode"]:checked').value);
  files.forEach((file) => form.append("files", file));
  if (text) form.append("files", new File([text], `text-${Date.now()}.txt`, { type: "text/plain;charset=utf-8" }));
  $("upload-button").disabled = true; setText("upload-error");
  try { state.draft = await api(fetch("/api/persona-drafts/upload", { method: "POST", body: form })); renderDraft(); }
  catch (reason) { setText("upload-error", reason); }
  finally { $("upload-button").disabled = false; }
}

function renderDraft() {
  $("draft-editor").classList.remove("is-hidden");
  $("draft-name").value = state.draft.suggested_name;
  $("draft-profile").value = state.draft.profile?.description || "";
  $("draft-status").textContent = state.draft.status;
  renderCandidates();
  renderDocuments($("document-list"), state.draft.documents, true);
  icons();
}

function renderCandidates() {
  const candidates = state.draft.candidates || [];
  $("candidate-picker").classList.toggle("is-hidden", !candidates.length);
  $("candidate-list").replaceChildren();
  for (const candidate of candidates) {
    const button = document.createElement("button");
    button.type = "button"; button.className = "candidate-option";
    button.classList.toggle("is-selected", candidate.id === state.draft.selected_candidate_id);
    const name = document.createElement("strong"); name.textContent = candidate.name;
    const description = document.createElement("span"); description.textContent = candidate.profile?.description || "";
    button.append(name, description); button.addEventListener("click", () => selectCandidate(candidate.id)); $("candidate-list").append(button);
  }
  $("confirm-draft").disabled = state.draft.persona_type === "character" && !state.draft.selected_candidate_id;
}

function renderDocuments(container, documents, allowRetry = false) {
  container.replaceChildren();
  if (!documents.length) return container.append(empty("暂无资料"));
  for (const item of documents) {
    const row = document.createElement("div"); row.className = "document-row";
    const name = document.createElement("span"); name.textContent = item.original_filename;
    const action = document.createElement("button"); action.type = "button"; action.textContent = `${item.status} · 预览`;
    action.addEventListener("click", () => openPreview(item)); row.append(name, action);
    if (allowRetry && item.status === "index_failed") {
      const retry = document.createElement("button"); retry.type = "button"; retry.textContent = "重试";
      retry.addEventListener("click", () => retryDocument(item.id)); row.append(retry);
    }
    container.append(row);
  }
}

async function selectCandidate(candidateId) {
  try { state.draft = await api(fetch(`/api/persona-drafts/${state.draft.id}/candidates/${candidateId}`, { method: "POST" })); renderDraft(); }
  catch (reason) { setText("upload-error", reason); }
}

async function saveDraft(required = false) {
  if (!state.draft) return;
  try {
    state.draft = await api(fetch(`/api/persona-drafts/${state.draft.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("draft-name").value.trim(), profile: { ...(state.draft.profile || {}), description: $("draft-profile").value.trim(), generation_mode: state.draft.mode } }) }));
    renderDraft();
  } catch (reason) { setText("upload-error", reason); if (required) throw reason; }
}

async function confirmDraft() {
  if (!state.draft) return;
  $("confirm-draft").disabled = true;
  try {
    await saveDraft(true);
    state.draft = await api(fetch(`/api/persona-drafts/${state.draft.id}/confirm`, { method: "POST" }));
    renderDraft(); await loadPersonas(state.draft.persona.id); switchView("chat"); pollDraft();
  } catch (reason) { setText("upload-error", reason); }
  finally { $("confirm-draft").disabled = false; }
}

async function retryDocument(documentId) {
  try { await api(fetch(`/api/documents/${documentId}/retry-index`, { method: "POST" })); pollDraft(); }
  catch (reason) { setText("upload-error", reason); }
}
function pollDraft() {
  clearTimeout(state.poller);
  if (!state.draft || state.draft.documents.every((item) => ["indexed", "index_failed"].includes(item.status))) return;
  state.poller = setTimeout(async () => { try { state.draft = await api(fetch(`/api/persona-drafts/${state.draft.id}`)); renderDraft(); pollDraft(); } catch (reason) { setText("upload-error", reason); } }, 1000);
}
function resetDraft() { clearTimeout(state.poller); state.draft = null; $("batch-form").reset(); $("draft-editor").classList.add("is-hidden"); summarizeFiles("document-files", "file-summary", "未选择文件"); setText("upload-error"); }

async function loadPersonas(selectId = "") {
  try {
    state.personas = await api(fetch("/api/personas"));
    fillPersonaSelect($("persona-select"), "选择角色");
    fillPersonaSelect($("edit-persona-select"), "请选择");
    renderPersonaList();
    if (selectId) { $("persona-select").value = selectId; selectPersona(); }
  } catch (reason) { setText("chat-error", reason); }
}
function fillPersonaSelect(select, placeholder) {
  const current = select.value;
  select.replaceChildren(new Option(placeholder, ""));
  state.personas.forEach((persona) => select.add(new Option(persona.name, persona.id)));
  if (state.personas.some((persona) => persona.id === current)) select.value = current;
}
function renderPersonaList() {
  $("persona-list").replaceChildren();
  for (const persona of state.personas) {
    const button = document.createElement("button"); button.type = "button"; button.className = "persona-item";
    button.classList.toggle("is-active", state.activePersona?.id === persona.id); button.textContent = persona.name;
    button.addEventListener("click", () => { $("persona-select").value = persona.id; selectPersona(); });
    $("persona-list").append(button);
  }
}
function selectPersona() {
  state.activePersona = state.personas.find((item) => item.id === $("persona-select").value) || null;
  state.conversationId = crypto.randomUUID(); state.pendingAction = null; renderConfirmation(); renderPersonaList();
  $("chat-title").textContent = state.activePersona?.name || "选择角色";
  $("send-question").disabled = !state.activePersona;
  $("chat-log").replaceChildren(empty(state.activePersona ? "开始对话" : "选择角色后开始聊天"));
}

async function loadEditPersona() {
  clearTimeout(state.editPoller);
  const id = $("edit-persona-select").value;
  state.editPersona = id ? await api(fetch(`/api/personas/${id}`)) : null;
  $("edit-persona-workspace").classList.toggle("is-hidden", !state.editPersona);
  $("delete-persona").disabled = !state.editPersona;
  if (!state.editPersona) return;
  $("edit-persona-name").value = state.editPersona.name;
  $("edit-persona-profile").value = state.editPersona.profile?.description || "";
  await loadEditDocuments();
}
function requestPersonaDeletion() {
  if (!state.editPersona) return;
  state.deletePersona = state.editPersona;
  setText("delete-persona-error");
  $("delete-persona-detail").textContent = `将永久删除“${state.deletePersona.name}”及其资料、记忆、向量和对话。此操作无法恢复。`;
  $("delete-persona-dialog").showModal();
}
async function confirmPersonaDeletion() {
  const persona = state.deletePersona;
  if (!persona) return;
  $("delete-persona-confirm").disabled = true;
  $("delete-persona-cancel").disabled = true;
  try {
    await api(fetch(`/api/personas/${persona.id}`, { method: "DELETE" }));
    $("delete-persona-dialog").close();
    state.deletePersona = null;
    state.editPersona = null;
    $("edit-persona-workspace").classList.add("is-hidden");
    $("delete-persona").disabled = true;
    const nextPersona = state.personas.find((item) => item.id !== persona.id) || null;
    state.activePersona = null;
    await loadPersonas(nextPersona?.id || "");
    if (!nextPersona) {
      $("persona-select").value = "";
      $("edit-persona-select").value = "";
      selectPersona();
    }
  } catch (reason) { setText("delete-persona-error", reason); }
  finally {
    $("delete-persona-confirm").disabled = false;
    $("delete-persona-cancel").disabled = false;
  }
}
async function loadEditDocuments() {
  if (!state.editPersona) return;
  const documents = await api(fetch(`/api/personas/${state.editPersona.id}/documents`));
  renderDocuments($("edit-document-list"), documents);
  if (documents.some((item) => item.status === "indexing")) state.editPoller = setTimeout(loadEditDocuments, 1200);
}
async function saveEditPersona(event) {
  event.preventDefault(); if (!state.editPersona) return;
  setText("edit-persona-status");
  try {
    state.editPersona = await api(fetch(`/api/personas/${state.editPersona.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("edit-persona-name").value.trim(), profile: { description: $("edit-persona-profile").value.trim() } }) }));
    setText("edit-persona-status", "已保存"); await loadPersonas(); $("edit-persona-select").value = state.editPersona.id;
  } catch (reason) { setText("edit-persona-status", reason); }
}
async function uploadEditDocuments(event) {
  event.preventDefault(); if (!state.editPersona) return;
  const files = [...$("edit-document-files").files]; const text = $("edit-direct-text").value.trim();
  if (!files.length && !text) return setText("edit-upload-error", "请选择资料或输入文本");
  const form = new FormData(); files.forEach((file) => form.append("files", file));
  if (text) form.append("files", new File([text], `text-${Date.now()}.txt`, { type: "text/plain;charset=utf-8" }));
  $("edit-upload-button").disabled = true; setText("edit-upload-error");
  try {
    const jobs = await api(fetch(`/api/knowledge-spaces/${state.editPersona.knowledge_space_id}/documents/upload`, { method: "POST", body: form }));
    await Promise.all(jobs.map((job) => api(fetch(`/api/documents/${job.id}/confirm`, { method: "POST" }))));
    $("edit-upload-form").reset(); summarizeFiles("edit-document-files", "edit-file-summary", "添加文件或图片"); await loadEditDocuments();
  } catch (reason) { setText("edit-upload-error", reason); }
  finally { $("edit-upload-button").disabled = false; }
}

async function submitQuestion(event) {
  event.preventDefault(); if (!state.activePersona) return;
  const question = $("question").value.trim(); if (!question) return;
  appendMessage("user", question); $("send-question").disabled = true; setText("chat-error");
  try {
    const result = await api(fetch(`/api/personas/${state.activePersona.id}/agent/query`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, conversation_id: state.conversationId }) }));
    handleAgentResult(result); $("question-form").reset();
  } catch (reason) { setText("chat-error", reason); }
  finally { $("send-question").disabled = Boolean(state.pendingAction); }
}
function appendMessage(type, text) {
  if ($("chat-log").querySelector(".empty-state")) $("chat-log").replaceChildren();
  const node = document.createElement("article"); node.className = `message message-${type}`;
  const label = document.createElement("strong"); label.textContent = type === "user" ? "你" : state.activePersona.name;
  const body = document.createElement("p"); body.textContent = text; node.append(label, body); $("chat-log").append(node); node.scrollIntoView({ block: "nearest" }); return node;
}
function handleAgentResult(result) {
  state.pendingAction = result.status === "pending_confirmation" ? { action: result.pending_action, specialist: result.specialist } : null;
  renderConfirmation(); $("send-question").disabled = Boolean(state.pendingAction) || !state.activePersona; if (result.answer) appendAnswer(result);
}
function appendAnswer(result) { const node = appendMessage("assistant", result.answer); if (result.evidence?.length) node.append(details("引用", result.evidence)); if (result.tool_calls?.length) node.append(details("工具", result.tool_calls)); if (result.trace?.length) node.append(details("检索", result.trace)); }
function renderConfirmation() {
  $("confirmation-panel").classList.toggle("is-hidden", !state.pendingAction); if (!state.pendingAction) return;
  const action = state.pendingAction.action || {}; $("confirmation-title").textContent = action.title || "确认操作"; $("confirmation-detail").textContent = `${action.target || "当前角色"} · ${JSON.stringify(action.arguments || {})}`;
}
async function resumeAgent(approved) {
  if (!state.pendingAction || !state.activePersona) return;
  $("confirm-action").disabled = true; $("cancel-action").disabled = true;
  try { const result = await api(fetch(`/api/personas/${state.activePersona.id}/agent/resume`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conversation_id: state.conversationId, specialist: state.pendingAction.specialist, approved }) })); handleAgentResult(result); }
  catch (reason) { setText("chat-error", reason); }
  finally { $("confirm-action").disabled = false; $("cancel-action").disabled = false; }
}

async function loadSettings() {
  try {
    const config = await api(fetch("/api/settings"));
    const keyPlaceholder = (configured) => configured ? "已保存，可输入新 Key 替换" : "请输入 API Key";
    $("openai-api-key").placeholder = keyPlaceholder(config.openai_api_key_configured);
    $("embedding-api-key").placeholder = keyPlaceholder(config.embedding_api_key_configured);
    $("web-search-api-key").placeholder = keyPlaceholder(config.web_search_api_key_configured);
    $("openai-base-url").value = config.openai_base_url; $("openai-model").value = config.openai_model;
    $("embedding-base-url").value = config.embedding_base_url; $("embedding-model").value = config.embedding_model;
    $("web-search-base-url").value = config.web_search_base_url;
    $("llm-provider").value = inferProvider(LLM_PRESETS, config.openai_base_url);
    $("embedding-provider").value = inferProvider(EMBEDDING_PRESETS, config.embedding_base_url);
    $("embedding-dimensions").value = config.embedding_dimensions;
    $("embedding-send-dimensions").checked = config.embedding_send_dimensions;
    $("web-search-provider").value = config.web_search_provider;
    state.savedEmbeddingDimensions = config.embedding_dimensions;
    renderEmbeddingWarning(); renderWebSearchSettings();
  } catch (reason) { setText("settings-status", reason); }
}
function normalizedUrl(value) { return value.trim().replace(/\/+$/, "").toLowerCase(); }
function inferProvider(presets, baseUrl) {
  const current = normalizedUrl(baseUrl || "");
  return Object.entries(presets).find(([, preset]) => normalizedUrl(preset.baseUrl) === current)?.[0] || "custom";
}
function applyLlmPreset() {
  const preset = LLM_PRESETS[$("llm-provider").value]; if (!preset) return;
  $("openai-base-url").value = preset.baseUrl; $("openai-model").value = preset.model;
}
function applyEmbeddingPreset() {
  const preset = EMBEDDING_PRESETS[$("embedding-provider").value]; if (!preset) return;
  $("embedding-base-url").value = preset.baseUrl; $("embedding-model").value = preset.model;
  $("embedding-dimensions").value = preset.dimensions; $("embedding-send-dimensions").checked = preset.sendDimensions;
  renderEmbeddingWarning();
}
function renderEmbeddingWarning() {
  const changed = Number($("embedding-dimensions").value) !== Number(state.savedEmbeddingDimensions);
  $("embedding-dimension-warning").classList.toggle("is-hidden", !changed);
}
function renderWebSearchSettings() {
  const provider = $("web-search-provider").value;
  const isCustom = provider === "custom";
  $("web-search-api-key").disabled = provider === "off";
  $("web-search-base-url-field").classList.toggle("is-hidden", !isCustom);
  $("web-search-base-url").disabled = !isCustom;
  const guide = WEB_SEARCH_GUIDES[provider] || WEB_SEARCH_GUIDES.off;
  const text = document.createElement("p"); text.textContent = guide.text;
  $("web-search-guide").replaceChildren(text);
  if (guide.href) {
    const link = document.createElement("a"); link.href = guide.href; link.target = "_blank"; link.rel = "noopener"; link.textContent = guide.link;
    const source = document.createElement("p"); source.textContent = `${guide.label}：`; source.append(link); $("web-search-guide").append(source);
  }
}
function requestSettingsSave(event) { event.preventDefault(); openSettingsConfirmation("save"); }
function requestSettingsReset() { openSettingsConfirmation("reset"); }
function openSettingsConfirmation(action) {
  state.settingsAction = action;
  const isSave = action === "save";
  $("settings-confirm-title").textContent = isSave ? "保存前确认" : "确认重置配置";
  $("settings-confirm-detail").textContent = isSave
    ? `对话：${$("llm-provider").selectedOptions[0].textContent} · ${$("openai-model").value.trim() || "未填写模型"}\nEmbedding：${$("embedding-provider").selectedOptions[0].textContent} · ${$("embedding-model").value.trim() || "未填写模型"} · ${$("embedding-dimensions").value || "未填写维度"} 维\n联网搜索：${$("web-search-provider").selectedOptions[0].textContent}\n将更新：${["openai-api-key", "embedding-api-key", "web-search-api-key"].filter((id) => $(id).value.trim()).length || "模型与连接配置"}`
    : "将清除本机前端保存的 LLM、Embedding、联网搜索配置和 Key。不会影响 .env 中的 MySQL、Milvus 或端口配置。";
  $("settings-confirm-submit").textContent = isSave ? "确认保存" : "确认重置";
  $("settings-confirm-dialog").showModal();
}
async function confirmSettingsAction() {
  const action = state.settingsAction; $("settings-confirm-dialog").close();
  if (action === "save") await saveSettings();
  if (action === "reset") await resetSettings();
}
async function saveSettings() {
  $("save-settings").disabled = true; setText("settings-status");
  const value = (id) => $(id).value.trim();
  try {
    const result = await api(fetch("/api/settings", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ openai_api_key: value("openai-api-key"), openai_base_url: value("openai-base-url"), openai_model: value("openai-model"), embedding_api_key: value("embedding-api-key"), embedding_base_url: value("embedding-base-url"), embedding_model: value("embedding-model"), embedding_dimensions: Number(value("embedding-dimensions")), embedding_send_dimensions: $("embedding-send-dimensions").checked, web_search_provider: $("web-search-provider").value, web_search_api_key: value("web-search-api-key"), web_search_base_url: value("web-search-base-url"), enable_web_fallback: $("web-search-provider").value !== "off" }) }));
    $("openai-api-key").value = ""; $("embedding-api-key").value = ""; $("web-search-api-key").value = ""; $("web-search-base-url").value = "";
    setText("settings-status", "已保存，可立即使用"); await loadSettings();
  } catch (reason) { setText("settings-status", reason); }
  finally { $("save-settings").disabled = false; }
}
async function resetSettings() {
  $("reset-settings").disabled = true; setText("settings-status");
  try {
    await api(fetch("/api/settings", { method: "DELETE" }));
    $("openai-api-key").value = ""; $("embedding-api-key").value = ""; $("web-search-api-key").value = ""; $("web-search-base-url").value = "";
    setText("settings-status", "配置已重置"); await loadSettings();
  } catch (reason) { setText("settings-status", reason); }
  finally { $("reset-settings").disabled = false; }
}

function openPreview(item) { $("preview-title").textContent = item.original_filename; $("preview-content").textContent = item.markdown_preview || item.error_message || "暂无内容"; $("preview-drawer").classList.add("is-open"); $("preview-backdrop").classList.add("is-open"); }
function closePreview() { $("preview-drawer").classList.remove("is-open"); $("preview-backdrop").classList.remove("is-open"); }
function details(label, data) { const node = document.createElement("details"); const summary = document.createElement("summary"); summary.textContent = `${label} (${data.length})`; const pre = document.createElement("pre"); pre.textContent = JSON.stringify(data, null, 2); node.append(summary, pre); return node; }
function empty(text) { const node = document.createElement("p"); node.className = "empty-state"; node.textContent = text; return node; }
