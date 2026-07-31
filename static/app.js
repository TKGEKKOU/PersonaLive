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
  embeddingKeyConfigured: false,
  webSearchKeyConfigured: false,
  realtimeSocket: null,
  realtimeTurnId: null,
  realtimeAnswerNode: null,
  realtimeExecutionPending: false,
  realtimeSubmissionPending: false,
  realtimePendingQuestion: "",
  realtimeAckTimer: null,
  realtimeBusy: false,
  agentRequestPending: false,
  asrConfigured: false,
  ttsConfigured: false,
  embeddingConfigured: false,
  embeddingInstalledModel: "",
  embeddingResourceStatus: null,
  openaiKeyConfigured: false,
  audioMode: "idle",
  audioStarting: false,
  audioRecorder: null,
  audioAbortController: null,
  audioOperationId: 0,
  audioStartedAt: 0,
  audioClock: null,
  editReferenceUrl: null,
  voiceStreamBuffer: "",
  voicePlaybackQueue: [],
  voicePlaybackActive: false,
  pendingReplyNode: null,
};
const $ = (id) => document.getElementById(id);
const LLM_PRESETS = {
  openai: { baseUrl: "https://api.openai.com/v1", model: "gpt-5.6-sol" },
  deepseek: { baseUrl: "https://api.deepseek.com", model: "deepseek-v4-flash" },
  qwen: { baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
};
const EMBEDDING_PRESETS = {
  qwen: { baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "text-embedding-v4", dimensions: 512, sendDimensions: true },
  managed_local: { baseUrl: "", model: "Qwen/Qwen3-Embedding-0.6B", dimensions: 1024, sendDimensions: false },
};
const WEB_SEARCH_GUIDES = {
  off: { text: "选择服务并填写 API Key 后，联网搜索才会启用。" },
  tavily: { text: "只需填写 Tavily API Key。适合通用英文与多语种网页搜索。", label: "官方入口", href: "https://app.tavily.com/", link: "Tavily" },
  bocha: { text: "只需填写博查 API Key。接口会返回适合 RAG 使用的网页摘要。", label: "官方入口", href: "https://open.bocha.cn/", link: "博查 AI" },
  custom: { text: "填写完整的 Web Search 接口地址和 API Key。接口需兼容博查/Bing 结果格式：POST 请求、Bearer 鉴权，并返回 data.webPages.value。" },
};

document.addEventListener("DOMContentLoaded", async () => {
  bindEvents();
  await Promise.all([loadStatus(), loadPersonas(), loadSettings(), loadEmbeddingStatus(), loadAsrStatus(), loadTtsStatus()]);
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
const API_KEY_FIELDS = {
  "openai-api-key": { field: "openai_api_key", configured: "openaiKeyConfigured" },
  "embedding-api-key": { field: "embedding_api_key", configured: "embeddingKeyConfigured" },
  "web-search-api-key": { field: "web_search_api_key", configured: "webSearchKeyConfigured" },
};

function bindEvents() {
  setSidebarPinned(false);
  prepareSettingsSections();
  $("sidebar-toggle").addEventListener("click", () => setSidebarPinned(!document.body.classList.contains("sidebar-pinned")));
  $("refresh-status").addEventListener("click", refreshSystemStatus);
  $("collapse-status").addEventListener("click", toggleStatusCards);
  document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => {
    switchView(button.dataset.view);
    setSidebarPinned(false);
  }));
  document.querySelectorAll("[data-target-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.targetView)));
  document.querySelectorAll('input[name="material-action"]').forEach((input) => input.addEventListener("change", () => switchMaterialMode(input.value)));
  $("document-files").addEventListener("change", () => summarizeFiles("document-files", "file-summary", "未选择文件"));
  $("edit-document-files").addEventListener("change", () => summarizeFiles("edit-document-files", "edit-file-summary", "添加文件或图片"));
  $("batch-form").addEventListener("submit", uploadDraft);
  $("reset-batch").addEventListener("click", resetDraft);
  $("save-draft").addEventListener("click", saveDraft);
  $("confirm-draft").addEventListener("click", confirmDraft);
  $("edit-persona-select").addEventListener("change", loadEditPersona);
  $("edit-persona-form").addEventListener("submit", saveEditPersona);
  $("edit-tts-reference").addEventListener("change", previewSelectedReference);
  $("edit-tts-confirm-upload").addEventListener("click", confirmReferenceUpload);
  $("edit-tts-preview-reference").addEventListener("click", playEditReference);
  $("edit-tts-generate-preview").addEventListener("click", generateEditPreview);
  $("edit-tts-open-settings").addEventListener("click", openTtsSettings);
  $("edit-tts-remove-reference").addEventListener("click", removeEditReference);
  $("edit-tts-enabled").addEventListener("change", syncEditTtsControls);
  $("edit-upload-form").addEventListener("submit", uploadEditDocuments);
  $("question-form").addEventListener("submit", submitQuestion);
  $("chat-process-toggle").addEventListener("click", toggleChatProcess);
  $("question").addEventListener("input", resizeComposer);
  $("cancel-generation").addEventListener("click", cancelRealtimeTurn);
  $("record-audio").addEventListener("click", () => state.audioMode === "recording" ? finishAudioRecording() : startAudioRecording());
  $("cancel-audio").addEventListener("click", cancelAudioActivity);
  $("confirm-action").addEventListener("click", () => resumeAgent(true));
  $("cancel-action").addEventListener("click", () => resumeAgent(false));
  $("question").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); if (!$("send-question").disabled) $("question-form").requestSubmit(); } });
  $("settings-form").addEventListener("submit", requestSettingsSave);
  $("reset-settings").addEventListener("click", requestSettingsReset);
  $("settings-confirm-cancel").addEventListener("click", () => $("settings-confirm-dialog").close());
  $("settings-confirm-submit").addEventListener("click", confirmSettingsAction);
  $("delete-persona").addEventListener("click", requestPersonaDeletion);
  $("delete-persona-cancel").addEventListener("click", () => $("delete-persona-dialog").close());
  $("delete-persona-confirm").addEventListener("click", confirmPersonaDeletion);
  $("llm-provider").addEventListener("change", applyLlmPreset);
  Object.keys(API_KEY_FIELDS).forEach((id) => {
    $(`toggle-${id}`).addEventListener("click", () => toggleApiKeyVisibility(id));
    $(`copy-${id}`).addEventListener("click", () => copyApiKey(id));
  });
  $("embedding-provider").addEventListener("change", applyEmbeddingPreset);
  $("managed-embedding-preset").addEventListener("change", applyManagedEmbeddingPreset);
  $("embedding-model").addEventListener("input", markEmbeddingSelectionChanged);
  ["embedding-base-url", "embedding-model", "embedding-api-key", "embedding-dimensions", "chunk-size", "chunk-overlap"].forEach((id) => $(id).addEventListener("input", renderEmbeddingWarning));
  $("web-search-enabled").addEventListener("change", renderWebSearchSettings);
  $("web-search-provider").addEventListener("change", renderWebSearchSettings);
  ["web-search-api-key", "web-search-base-url"].forEach((id) => $(id).addEventListener("input", renderWebSearchSettings));
  $("clear-conversation").addEventListener("click", clearConversation);
  $("save-asr").addEventListener("click", saveAsrConfig);
  $("install-asr").addEventListener("click", installAsr);
  $("cancel-asr").addEventListener("click", cancelAsr);
  $("remove-asr").addEventListener("click", removeAsr);
  $("open-asr-directory").addEventListener("click", openAsrDirectory);
  $("install-embedding").addEventListener("click", installEmbedding);
  $("cancel-embedding").addEventListener("click", cancelEmbedding);
  $("remove-embedding").addEventListener("click", removeEmbedding);
  $("open-embedding-directory").addEventListener("click", openEmbeddingDirectory);
  $("tts-enabled").addEventListener("change", saveTtsConfig);
  $("tts-use-gpu").addEventListener("change", saveTtsConfig);
  $("install-tts").addEventListener("click", installTts);
  $("cancel-tts").addEventListener("click", cancelTts);
  $("remove-tts").addEventListener("click", removeTts);
  $("open-tts-directory").addEventListener("click", openTtsDirectory);
  $("preview-tts").addEventListener("click", previewTts);
  $("close-preview").addEventListener("click", closePreview);
  $("preview-backdrop").addEventListener("click", closePreview);
  $("chat-persona-toggle").addEventListener("click", togglePersonaDrawer);
  document.addEventListener("click", (event) => { if (!event.target.closest(".chat-persona-picker")) closePersonaMenu(); });
  $("chat-settings-toggle").addEventListener("click", (event) => { event.stopPropagation(); toggleChatSettingsMenu(); });
  document.addEventListener("click", (event) => { if (!event.target.closest(".chat-settings")) closeChatSettingsMenu(); });
  document.querySelectorAll("#chat-settings-menu button").forEach((button) => button.addEventListener("click", closeChatSettingsMenu));
  $("assistant-voice-toggle").checked = localStorage.getItem("personalive:assistant-voice") !== "off";
  $("assistant-voice-toggle").addEventListener("change", () => localStorage.setItem("personalive:assistant-voice", $("assistant-voice-toggle").checked ? "on" : "off"));
  document.querySelectorAll("[data-collapsible]").forEach((section) => section.addEventListener("toggle", () => {
    const label = section.querySelector(".section-toggle-label"); if (label) label.textContent = section.open ? "收起" : "展开";
  }));
  // 对话是主要工作区，应用启动后直接进入，资料和设置通过侧栏切换。
  switchView("chat");
}

// 配置项直接展示，获取途径和补充说明保持收起，减少设置页首屏的信息密度。
function prepareSettingsSections() {
  const sections = [...document.querySelectorAll(".settings-section")];
  sections.forEach((section) => { section.open = false; });
  document.querySelectorAll(".settings-help, .inline-guide").forEach((guide) => { guide.open = false; });

  const asrSection = sections.find((section) => section.querySelector("#asr-enabled"));
  if (asrSection && !asrSection.querySelector(".settings-help")) {
    const guide = document.createElement("details");
    guide.className = "settings-help";
    const summary = document.createElement("summary");
    summary.textContent = "参数说明与获取途径";
    const description = document.createElement("p");
    description.textContent = "直接点击“自动下载安装”可从国内 ModelScope 获取本地 ASR 环境和模型。Python、模型目录与 FFmpeg 仅用于接入已有本地资源，留空时由应用自动检测；下载失败后可重试。";
    guide.append(summary, description);
    asrSection.querySelector(".settings-grid")?.after(guide);
  }

  const ttsGuide = $("tts-guide");
  if (ttsGuide) {
    ttsGuide.open = false;
    ttsGuide.querySelector("summary").textContent = "参数说明与获取途径";
    ttsGuide.querySelector("p").textContent = "直接点击“自动下载安装”可从国内 ModelScope 获取本地 TTS 模型。安装完成后，在角色资料中上传参考音频即可生成角色语音；GPU 加速可按设备情况启用。";
  }
}

// 默认保持窄轨道；按钮只控制是否固定展开，普通悬停展开由 CSS 负责。
function setSidebarPinned(pinned) {
  document.body.classList.toggle("sidebar-pinned", pinned);
  const button = $("sidebar-toggle");
  const label = pinned ? "取消固定展开" : "固定展开侧边栏";
  button.setAttribute("aria-label", label);
  button.setAttribute("aria-pressed", String(pinned));
  button.title = label;
}

function setApiKeyVisibilityIcon(inputId, visible) {
  const button = $(`toggle-${inputId}`);
  const icon = document.createElement("i");
  icon.setAttribute("data-lucide", visible ? "eye-off" : "eye");
  button.replaceChildren(icon);
  button.setAttribute("aria-label", visible ? "隐藏 API Key" : "显示 API Key");
  button.title = button.getAttribute("aria-label");
  icons();
}

async function ensureApiKeyValue(inputId) {
  const input = $(inputId);
  if (input.value) return input.value;
  const config = API_KEY_FIELDS[inputId];
  if (!state[config.configured]) {
    setText("settings-status", "尚未配置该 API Key");
    return "";
  }
  const result = await api(fetch("/api/settings/reveal-key", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-PersonaLive-Request": "web" },
    body: JSON.stringify({ field: config.field }),
  }));
  input.value = result.value || "";
  return input.value;
}

async function toggleApiKeyVisibility(inputId) {
  const input = $(inputId);
  try {
    if (input.type === "text") {
      input.type = "password";
      setApiKeyVisibilityIcon(inputId, false);
      return;
    }
    if (!await ensureApiKeyValue(inputId)) return;
    input.type = "text";
    setApiKeyVisibilityIcon(inputId, true);
  } catch (reason) { setText("settings-status", reason); }
}

async function copyApiKey(inputId) {
  try {
    const value = await ensureApiKeyValue(inputId);
    if (!value) return;
    await navigator.clipboard.writeText(value);
    setText("settings-status", "API Key 已复制");
  } catch (reason) { setText("settings-status", `复制失败：${reason.message || reason}`); }
}

function resetApiKeyInputs() {
  Object.keys(API_KEY_FIELDS).forEach((id) => {
    $(id).value = "";
    $(id).type = "password";
    setApiKeyVisibilityIcon(id, false);
  });
}

function switchView(view) {
  if (view !== "chat" && (state.audioStarting || state.audioMode !== "idle")) cancelAudioActivity();
  for (const name of ["upload", "chat", "settings"]) $(name + "-view").classList.toggle("is-hidden", name !== view);
  document.querySelectorAll("[data-view]").forEach((button) => button.classList.toggle("is-active", button.dataset.view === view));
}

function switchMaterialMode(mode) {
  $("create-material-panel").classList.toggle("is-hidden", mode !== "create");
  $("edit-material-panel").classList.toggle("is-hidden", mode !== "edit");
}

async function loadStatus() {
  try {
    const status = await api(fetch("/api/status"));
    renderServiceStatus("mysql", "MySQL", status.mysql);
    renderServiceStatus("milvus", "Milvus", status.milvus);
    renderSystemStatusDetail(status);
  } catch {
    renderServiceStatus("mysql", "MySQL", "unavailable");
    renderServiceStatus("milvus", "Milvus", "unavailable");
    setText("system-status-detail", "无法获取详细状态，请稍后重试。");
  }
}
function refreshSystemStatus() {
  const button = $("refresh-status");
  if (button) button.disabled = true;
  Promise.all([loadStatus(), loadEmbeddingStatus(), loadAsrStatus(), loadTtsStatus()]).finally(() => { if (button) button.disabled = false; });
}
function toggleStatusCards() {
  const collapsed = document.body.classList.toggle("status-cards-collapsed");
  const button = $("collapse-status");
  if (button) {
    const label = collapsed ? "展开详情" : "折叠详情";
    button.setAttribute("aria-pressed", String(collapsed));
    button.title = label;
    button.setAttribute("aria-label", label);
  }
}
function formatDuration(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60), s = Math.floor(seconds % 60);
  if (h > 0) return `${h} 小时 ${m} 分`;
  if (m > 0) return `${m} 分 ${s} 秒`;
  return `${s} 秒`;
}
function renderSystemStatusDetail(status) {
  const base = (path) => { const text = String(path || ""); const parts = text.split(/[\\/]/); return parts[parts.length - 1] || text; };
  const setDetail = (service, lines) => {
    const node = document.querySelector(`[data-service-status="${service}"] [data-status-detail]`);
    if (!node) return;
    const anchor = node.querySelector("a.status-card-text-link");
    node.querySelectorAll("div").forEach((item) => item.remove());
    lines.filter(Boolean).forEach((line) => {
      const div = document.createElement("div");
      div.textContent = line;
      if (anchor) node.insertBefore(div, anchor); else node.append(div);
    });
  };
  const setValue = (service, text) => {
    const node = document.querySelector(`[data-service-status="${service}"] [data-status-value]`);
    if (node) node.textContent = text || "—";
  };

  const config = status.config || {};
  const providerNames = { openai: "OpenAI", deepseek: "DeepSeek", qwen: "通义千问", custom: "自定义" };
  const llmConfigured = Boolean(config.llm_provider && config.llm_provider !== "未配置");
  const llmCard = document.querySelector('[data-service-status="llm"]');
  if (llmCard) llmCard.classList.toggle("is-ok", llmConfigured);
  setValue("llm", llmConfigured ? "正常" : "未配置");
  setDetail("llm", [config.openai_model, config.openai_base_url]);

  const resources = status.resources || {};
  const embedding = resources.embedding || {};
  const embeddingDevice = embedding.actual_device ? embedding.actual_device.toUpperCase() : "";
  setDetail("embedding", embedding.ready
    ? [`${base(embedding.model_id)}${embeddingDevice ? ` · ${embeddingDevice}` : ""}`, embedding.dimensions ? `${embedding.dimensions} 维` : ""]
    : [embedding.installing ? "安装中" : (embedding.error || "未安装")]);

  const asr = resources.asr || {};
  setDetail("asr", asr.ready
    ? [`${base(asr.resolved_model)}`, "按需启动 · 首次语音消息时运行"]
    : [asr.installing ? "安装中" : (asr.error || "未安装")]);

  const tts = resources.tts || {};
  setDetail("tts", tts.ready
    ? [`${base(tts.model_dir || tts.runtime)}`, `GPU ${tts.use_gpu ? "加速" : "关闭"} · 首次合成时运行`]
    : [tts.installing ? "安装中" : (tts.error || "未安装")]);

  setDetail("mysql", status.mysql === "ok"
    ? [status.mysql_version ? `MySQL ${status.mysql_version} · 已连接` : "本地数据库已连接"]
    : [status.mysql === "unavailable" ? "连接失败" : "检查中"]);
  setDetail("milvus", [
    status.milvus === "ok"
      ? "知识库已就绪"
      : status.milvus === "collection_missing"
        ? "缺少集合，请重建"
        : status.milvus === "unavailable" ? "服务不可用" : "检查中",
    status.collection ? `集合 ${status.collection}` : "",
  ]);

  const app = status.app || {};
  setValue("machine", app.version ? `v${app.version}` : "—");
  const machineLines = [
    Number.isFinite(app.uptime_seconds) ? `运行 ${formatDuration(app.uptime_seconds)}` : "",
    app.python ? `Python ${app.python}` : "",
    app.system ? `系统 ${app.system}${app.system_build ? ` · ${app.system_build}` : ""}` : "",
  ];
  const memory = status.memory || {};
  if (memory.total_gb) machineLines.push(`内存 可用 ${memory.available_gb} / ${memory.total_gb} GB`);
  const disk = status.disk || {};
  if (disk.system && disk.system.total_gb) machineLines.push(`磁盘 ${disk.system.drive} 剩余 ${disk.system.free_gb} GB${disk.project && disk.project.total_gb ? ` · ${disk.project.drive} 剩余 ${disk.project.free_gb} GB` : ""}`);
  const gpu = status.gpu;
  machineLines.push(gpu && gpu.name ? `GPU ${gpu.name} · ${gpu.vram_used_gb}/${gpu.vram_total_gb} GB` : "GPU 未检测到 NVIDIA 显卡");
  setDetail("machine", machineLines);
}
function keyStateLabel(configured, input) {
  const typed = input.value.trim();
  if (typed) return "将保存新 Key";
  return configured ? "已保存（留空不修改）" : "未填写";
}
function buildConfigDetail() {
  const webEnabled = $("web-search-enabled").checked;
  const lines = [
    `LLM：${$("llm-provider").selectedOptions[0].textContent} · ${$("openai-model").value.trim() || "未填写模型"}`,
    `对话 Base URL：${$("openai-base-url").value.trim() || "未填写"}`,
    `对话 API Key：${keyStateLabel(state.openaiKeyConfigured, $("openai-api-key"))}`,
    "",
    `Embedding：${$("embedding-provider").selectedOptions[0].textContent} · ${$("embedding-model").value.trim() || "未填写模型"}`,
    `Embedding 来源：${$("embedding-model-source").selectedOptions[0].textContent} · 设备：${$("embedding-device").selectedOptions[0].textContent}`,
    `Embedding 维度：${$("embedding-dimensions").value || "未填写"}${$("embedding-send-dimensions").checked ? "（发送 dimensions）" : ""}`,
    `Embedding Base URL：${$("embedding-base-url").value.trim() || "未填写"}`,
    `Embedding API Key：${keyStateLabel(state.embeddingKeyConfigured, $("embedding-api-key"))}`,
    "",
    `文档切分：长度 ${$("chunk-size").value} / 重叠 ${$("chunk-overlap").value}`,
    "",
    `联网搜索：${webEnabled ? "开启" : "关闭"}`,
  ];
  if (webEnabled) {
    lines.push(`搜索服务：${$("web-search-provider").selectedOptions[0].textContent}`);
    lines.push(`搜索 API Key：${keyStateLabel(state.webSearchKeyConfigured, $("web-search-api-key"))}`);
    if ($("web-search-provider").value === "custom") lines.push(`搜索接口地址：${$("web-search-base-url").value.trim() || "未填写"}`);
  }
  return lines.join("\n");
}
function renderServiceStatus(service, label, value, state = value) {
  const node = document.querySelector(`[data-service-status="${service}"]`);
  if (!node) return;
  const stateLabel = {
    ok: "正常",
    collection_missing: "缺少集合",
    unavailable: "不可用",
    ready: "正常",
    installing: "安装中",
    not_installed: "未安装",
    disabled: "已关闭",
  }[value] || value || "不可用";
  node.classList.toggle("is-ok", state === "ok" || state === "ready");
  node.classList.toggle("is-pending", state === "installing");
  node.classList.toggle("is-warning", ["not_installed", "disabled", "collection_missing"].includes(state));
  const labelNode = node.querySelector("[data-status-label]");
  const valueNode = node.querySelector("[data-status-value]");
  if (labelNode) labelNode.textContent = label;
  if (valueNode) valueNode.textContent = stateLabel;
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
    renderDraft(); await loadPersonas(state.draft.persona.id); switchView("upload"); document.querySelector('input[name="material-action"][value="edit"]').checked = true; switchMaterialMode("edit"); $("edit-persona-select").value = state.draft.persona.id; await loadEditPersona(); setText("edit-persona-status", "角色已创建，请配置参考音色后保存"); pollDraft();
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
    fillPersonaSelect($("edit-persona-select"), "请选择");
    renderPersonaList();
    if (selectId) await selectPersona(selectId);
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
    button.setAttribute("role", "menuitem");
    button.addEventListener("click", () => selectPersona(persona.id));
    $("persona-list").append(button);
  }
}
async function selectPersona(personaId = "") {
  cancelAudioActivity();
  setText("audio-status");
  closeRealtime();
  state.activePersona = state.personas.find((item) => item.id === personaId) || null;
  if (state.activePersona) {
    const key = `personalive:conversation:${state.activePersona.id}`;
    state.conversationId = localStorage.getItem(key) || crypto.randomUUID();
    localStorage.setItem(key, state.conversationId);
  } else state.conversationId = crypto.randomUUID();
  state.pendingAction = null; renderConfirmation(); renderPersonaList();
  $("chat-title").textContent = state.activePersona?.name || "选择角色";
  $("send-question").disabled = !state.activePersona;
  $("chat-log").replaceChildren(empty(state.activePersona ? "开始对话" : "选择角色后开始聊天"));
  $("clear-conversation").disabled = !state.activePersona;
  closePersonaMenu();
  if (state.activePersona) { await loadConversationMessages(); connectRealtime(); }
  updateComposerControls();
}

function closeRealtime() {
  const socket = state.realtimeSocket;
  clearTimeout(state.realtimeAckTimer);
  state.realtimeSocket = null;
  state.realtimeTurnId = null;
  state.realtimeAnswerNode = null;
  state.realtimeExecutionPending = false;
  state.realtimeSubmissionPending = false;
  state.agentRequestPending = false;
  state.realtimePendingQuestion = "";
  state.realtimeAckTimer = null;
  setRealtimeBusy(false);
  if (socket && socket.readyState < WebSocket.CLOSING) socket.close();
}

function connectRealtime() {
  if (!state.activePersona) return;
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const url = `${scheme}://${location.host}/ws/personas/${state.activePersona.id}/conversations/${state.conversationId}`;
  const socket = new WebSocket(url);
  state.realtimeSocket = socket;
  socket.addEventListener("message", (message) => {
    if (socket !== state.realtimeSocket) return;
    try { handleRealtimeEvent(JSON.parse(message.data)); }
    catch { setText("chat-error", "实时会话返回了无效数据"); }
  });
  socket.addEventListener("close", () => {
    if (socket !== state.realtimeSocket) return;
    state.realtimeSocket = null;
    if (state.realtimeSubmissionPending) failRealtimeSubmission("实时连接在接收请求前中断，请重新操作");
    if (state.realtimeTurnId) {
      state.realtimeTurnId = null;
      state.realtimeAnswerNode = null;
      setRealtimeBusy(false);
      setText("chat-error", "实时连接已中断，请重新发送消息");
    }
    state.realtimeExecutionPending = false;
    setRealtimeBusy(false);
  });
  socket.addEventListener("error", () => {
    if (socket === state.realtimeSocket) setText("chat-error", "实时连接不可用，将使用普通对话");
  });
}

function setRealtimeBusy(busy) {
  state.realtimeBusy = busy;
  setText("question-status", busy ? "角色正在生成回复…" : "");
  $("question-form").classList.toggle("is-generating", busy);
  $("cancel-generation").classList.toggle("is-hidden", !busy);
  $("cancel-generation").disabled = !busy;
  $("confirm-action").disabled = busy;
  $("cancel-action").disabled = busy;
  updateComposerControls();
}

function toggleChatProcess() {
  const body = $("chat-process-body");
  const hidden = body.classList.toggle("is-hidden");
  $("chat-process-toggle").setAttribute("aria-expanded", String(!hidden));
}

function resetChatProcess() {
  $("chat-process-panel").classList.add("is-hidden");
  $("chat-process-body").classList.add("is-hidden");
  $("chat-process-toggle").setAttribute("aria-expanded", "false");
  $("chat-process-summary").textContent = "等待中";
  $("chat-process-content").replaceChildren();
}

function renderChatProcess(result) {
  const traces = result?.trace || [];
  const toolCalls = result?.tool_calls || [];
  if (!traces.length && !toolCalls.length) return;
  $("chat-process-panel").classList.remove("is-hidden");
  $("chat-process-summary").textContent = `工具 ${toolCalls.length} · 检索 ${traces.length}`;
  const content = $("chat-process-content"); content.replaceChildren();
  const toolCounts = new Map();
  for (const tool of toolCalls) { const name = tool.name || String(tool); toolCounts.set(name, (toolCounts.get(name) || 0) + 1); }
  const nodeLabels = { route_query: "问题路由", retrieve: "知识检索", batch_grade_documents: "证据筛选", generate: "生成回答", quality_gate: "质量门禁", prepare_correction: "自我纠正", rewrite_query: "改写问题", web_search: "联网检索" };
  const items = [
    ...[...toolCounts].map(([name, count]) => ({ label: name, value: `${count} 次` })),
    ...traces.map((x) => ({ label: nodeLabels[x.node] || x.node || "处理步骤", value: x.document_count != null ? `${x.document_count} 个片段` : "完成" })),
  ];
  for (const item of items) {
    const row = document.createElement("div"); row.className = "chat-process-row";
    const label = document.createElement("span"); label.textContent = item.label;
    const value = document.createElement("span"); value.textContent = item.value;
    row.append(label, value); content.append(row);
  }
}

function showReplyLoading() {
  if (state.pendingReplyNode) return state.pendingReplyNode;
  const node = appendMessage("assistant", ""); node.classList.add("message-loading");
  const body = node.querySelector("p"); body.classList.add("loading-bubble");
  body.innerHTML = "<span></span><span></span><span></span>";
  state.pendingReplyNode = node; return node;
}

function replaceReplyLoading(node, text) {
  if (!node) return appendMessage("assistant", text);
  node.classList.remove("message-loading");
  const body = node.querySelector("p"); body.classList.remove("loading-bubble"); body.textContent = text;
  state.pendingReplyNode = null; return node;
}

function handleRealtimeEvent(event) {
  if (event.type === "session.ready") {
    state.realtimeExecutionPending = false;
    setRealtimeBusy(false);
    return;
  }
  if (event.type === "session.pong" || event.type === "agent.status") return;
  if (event.type === "turn.started") {
    clearRealtimeSubmission();
    state.realtimeTurnId = event.turn_id;
    state.realtimeAnswerNode = null;
    state.voiceStreamBuffer = "";
    resetChatProcess();
    showReplyLoading();
    setRealtimeBusy(true);
    return;
  }
  if (event.turn_id && event.turn_id !== state.realtimeTurnId) return;
  if (event.type === "text.delta") {
    if (!state.realtimeAnswerNode) state.realtimeAnswerNode = showReplyLoading();
    state.realtimeAnswerNode.classList.remove("message-loading");
    state.realtimeAnswerNode.querySelector("p").classList.remove("loading-bubble");
    state.realtimeAnswerNode.querySelector("p").textContent += event.text;
    collectStreamVoice(event.text, state.realtimeAnswerNode);
  } else if (event.type === "text.final") {
    if (!state.realtimeAnswerNode && event.answer) state.realtimeAnswerNode = showReplyLoading();
    if (state.realtimeAnswerNode) {
      replaceReplyLoading(state.realtimeAnswerNode, event.answer || state.realtimeAnswerNode.querySelector("p").textContent);
      flushStreamVoice(true, state.realtimeAnswerNode);
      renderChatProcess(event);
    }
    state.pendingAction = null;
    renderConfirmation();
    state.realtimeTurnId = null;
    state.realtimeAnswerNode = null;
    state.voiceStreamBuffer = "";
    setRealtimeBusy(false);
  } else if (event.type === "confirmation.required") {
    state.pendingAction = { action: event.pending_action, specialist: event.specialist };
    state.realtimeTurnId = null;
    state.realtimeAnswerNode = null;
    state.voiceStreamBuffer = "";
    renderConfirmation();
    setRealtimeBusy(false);
  } else if (event.type === "turn.cancelled") {
    state.realtimeTurnId = null;
    state.realtimeAnswerNode = null;
    state.realtimeExecutionPending = true;
    setRealtimeBusy(false);
  } else if (event.type === "error") {
    if (state.realtimeSubmissionPending) failRealtimeSubmission(event.message || "实时会话未接收消息，请重新发送");
    setText("chat-error", event.message || "实时会话发生错误");
    state.realtimeTurnId = null;
    state.realtimeAnswerNode = null;
    if (event.code !== "turn_in_progress") state.realtimeExecutionPending = false;
    setRealtimeBusy(false);
  }
}

function sendRealtime(payload) {
  if (state.realtimeSocket?.readyState !== WebSocket.OPEN) return false;
  try { state.realtimeSocket.send(JSON.stringify(payload)); return true; }
  catch { return false; }
}

function clearRealtimeSubmission() {
  clearTimeout(state.realtimeAckTimer);
  state.realtimeAckTimer = null;
  state.realtimeSubmissionPending = false;
  state.agentRequestPending = false;
  state.realtimePendingQuestion = "";
  updateComposerControls();
}

function failRealtimeSubmission(message) {
  const question = state.realtimePendingQuestion;
  clearRealtimeSubmission();
  if (question) $("question").value = question;
  setText("chat-error", message);
  setRealtimeBusy(false);
}

function awaitRealtimeAcknowledgement(question) {
  state.realtimeSubmissionPending = true;
  state.agentRequestPending = true;
  state.realtimePendingQuestion = question;
  clearTimeout(state.realtimeAckTimer);
  state.realtimeAckTimer = setTimeout(() => {
    if (!state.realtimeSubmissionPending) return;
    failRealtimeSubmission("实时会话响应超时，请重新发送");
    state.realtimeSocket?.close();
  }, 5000);
  updateComposerControls();
}

function cancelRealtimeTurn() {
  if (state.realtimeTurnId) sendRealtime({ type: "generation.cancel" });
}

function updateComposerControls() {
  const conversationBusy = isConversationBusy();
  const audioActive = state.audioStarting || state.audioMode !== "idle";
  $("question-form").classList.toggle("is-audio-active", audioActive && !state.realtimeBusy);
  $("record-audio").classList.toggle("is-hidden", state.realtimeBusy);
  $("record-audio").disabled = state.audioMode === "transcribing" || !state.asrConfigured || !state.activePersona || conversationBusy;
  $("cancel-audio").classList.toggle("is-hidden", !audioActive);
  $("send-question").disabled = conversationBusy || audioActive || !state.activePersona;
  $("confirm-action").disabled = state.realtimeBusy || audioActive;
  $("cancel-action").disabled = state.realtimeBusy || audioActive;
}

function isConversationBusy() {
  return state.realtimeBusy || state.agentRequestPending || state.realtimeSubmissionPending || state.realtimeExecutionPending || Boolean(state.pendingAction);
}

function setAudioButton(iconName, title, className = "") {
  const button = $("record-audio");
  const icon = document.createElement("i");
  icon.dataset.lucide = iconName;
  button.replaceChildren(icon);
  button.title = title;
  button.setAttribute("aria-label", title);
  button.classList.toggle("is-recording", className === "recording");
  button.classList.toggle("is-transcribing", className === "transcribing");
  icons();
}

function renderAudioState() {
  clearInterval(state.audioClock);
  state.audioClock = null;
  if (state.audioStarting) {
    setAudioButton("loader-circle", "正在请求麦克风权限", "transcribing");
    setText("audio-status", "正在请求麦克风权限");
  } else if (state.audioMode === "recording") {
    setAudioButton("square", "完成录音", "recording");
    updateAudioClock();
    state.audioClock = setInterval(updateAudioClock, 1000);
  } else if (state.audioMode === "transcribing") {
    setAudioButton("loader-circle", "正在识别语音", "transcribing");
    setText("audio-status", "正在识别语音");
  } else {
    setAudioButton("mic", state.asrConfigured ? "开始录音" : "请先配置语音识别");
    setText("audio-status");
  }
  updateComposerControls();
}

function updateAudioClock() {
  const elapsed = Math.max(0, Math.floor((Date.now() - state.audioStartedAt) / 1000));
  const minutes = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const seconds = String(elapsed % 60).padStart(2, "0");
  setText("audio-status", `正在录音 ${minutes}:${seconds}`);
}

function audioErrorMessage(error) {
  if (error?.name === "NotAllowedError") return "麦克风权限被拒绝，请在浏览器中允许访问后重试";
  if (error?.name === "NotFoundError") return "未检测到可用麦克风";
  if (error?.message === "Audio recording is not supported") return "当前浏览器不支持录音";
  return error?.message || "录音失败，请重试";
}

async function startAudioRecording() {
  if (!state.asrConfigured || !state.activePersona || isConversationBusy() || state.audioMode !== "idle" || state.audioStarting) return;
  const operationId = ++state.audioOperationId;
  state.audioStarting = true;
  setText("chat-error");
  const recorder = new window.BrowserAudioRecorder({
    maxDurationMs: 120000,
    onLimit: () => { if (state.audioRecorder === recorder && state.audioMode === "recording") void finishAudioRecording(); },
    onError: (error) => handleUnexpectedAudioStop(recorder, error),
    onUnexpectedStop: () => handleUnexpectedAudioStop(recorder, new Error("录音意外停止，请重试")),
  });
  state.audioRecorder = recorder;
  renderAudioState();
  try {
    await recorder.start();
    if (operationId !== state.audioOperationId) return void recorder.cancel();
    state.audioStarting = false;
    state.audioMode = "recording";
    state.audioStartedAt = Date.now();
    renderAudioState();
  } catch (error) {
    if (operationId !== state.audioOperationId) return;
    state.audioStarting = false;
    state.audioRecorder = null;
    state.audioMode = "idle";
    setText("chat-error", audioErrorMessage(error));
    renderAudioState();
  }
}

function handleUnexpectedAudioStop(recorder, error) {
  if (state.audioRecorder !== recorder) return;
  state.audioOperationId += 1;
  state.audioRecorder = null;
  state.audioStarting = false;
  state.audioMode = "idle";
  setText("chat-error", audioErrorMessage(error));
  renderAudioState();
}

async function finishAudioRecording() {
  if (state.audioMode !== "recording" || !state.audioRecorder) return;
  const operationId = state.audioOperationId;
  const recorder = state.audioRecorder;
  state.audioMode = "transcribing";
  renderAudioState();
  try {
    const blob = await recorder.finish();
    state.audioRecorder = null;
    if (operationId !== state.audioOperationId || !blob) return;
    const extension = audioExtension(blob.type);
    const form = new FormData();
    form.append("file", blob, `recording-${Date.now()}.${extension}`);
    const controller = new AbortController();
    state.audioAbortController = controller;
    const message = await api(fetch(`/api/personas/${state.activePersona.id}/conversations/${state.conversationId}/voice-messages`, { method: "POST", headers: { "X-PersonaLive-Request": "web" }, body: form, signal: controller.signal }));
    if (operationId !== state.audioOperationId) return;
    appendAudioMessage(message);
    const result = await api(fetch(`/api/voice-messages/${message.id}/transcribe`, { method: "POST", headers: { "X-PersonaLive-Request": "web" }, signal: controller.signal }));
    updateAudioMessage(result.message);
    handleAgentResult(result.turn);
  } catch (error) {
    if (operationId === state.audioOperationId && error?.name !== "AbortError") {
      setText("chat-error", audioErrorMessage(error));
      await loadConversationMessages();
    }
  } finally {
    if (operationId === state.audioOperationId) {
      state.audioAbortController = null;
      state.audioMode = "idle";
      renderAudioState();
    }
  }
}

function audioExtension(contentType) {
  if (contentType.includes("ogg")) return "ogg";
  if (contentType.includes("mp4") || contentType.includes("m4a")) return "m4a";
  if (contentType.includes("mpeg")) return "mp3";
  if (contentType.includes("wav")) return "wav";
  return "webm";
}

function cancelAudioActivity() {
  const wasActive = state.audioStarting || state.audioMode !== "idle";
  state.audioOperationId += 1;
  state.audioAbortController?.abort();
  state.audioAbortController = null;
  const recorder = state.audioRecorder;
  state.audioRecorder = null;
  state.audioStarting = false;
  state.audioMode = "idle";
  if (recorder) void recorder.cancel().catch(() => {});
  renderAudioState();
  if (wasActive) setText("audio-status", "录音已取消");
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
  $("edit-tts-enabled").checked = Boolean(state.editPersona.profile?.tts?.enabled);
  $("edit-tts-auto-play").checked = state.editPersona.profile?.tts?.auto_play !== false;
  $("edit-tts-reference").value = "";
  await loadEditReference();
  syncEditTtsControls();
  await loadEditDocuments();
}

function togglePersonaDrawer() {
  const menu = $("chat-persona-menu");
  const open = menu.classList.toggle("is-hidden");
  $("chat-persona-toggle").setAttribute("aria-expanded", String(!open));
}
function closePersonaMenu() { $("chat-persona-menu").classList.add("is-hidden"); $("chat-persona-toggle").setAttribute("aria-expanded", "false"); }
function toggleChatSettingsMenu() {
  const menu = $("chat-settings-menu");
  const button = $("chat-settings-toggle");
  if (!menu || !button) return;
  const open = menu.classList.toggle("is-hidden") === false;
  button.setAttribute("aria-expanded", String(open));
}
function closeChatSettingsMenu() {
  const menu = $("chat-settings-menu");
  const button = $("chat-settings-toggle");
  if (!menu || !button || menu.classList.contains("is-hidden")) return;
  menu.classList.add("is-hidden");
  button.setAttribute("aria-expanded", "false");
}
async function loadEditReference() {
  if (!state.editPersona) return;
  const info = await api(fetch(`/api/tts/personas/${state.editPersona.id}/reference`, { headers: { "X-PersonaLive-Request": "web" } }));
  setText("edit-tts-reference-status", info.configured ? `已配置：${info.name}${info.count > 1 ? `（已合并 ${info.count} 条）` : ""}` : "未配置参考音色");
  $("edit-tts-preview-reference").disabled = !info.configured;
  $("edit-tts-remove-reference").disabled = !info.configured;
  syncEditTtsPreview(info.configured);
  markTtsStep("select", info.configured ? "已配置参考音色" : "选择 WAV 音频");
  markTtsStep("upload", info.configured ? "音色文件已处理" : "点击确认上传并处理");
  markTtsStep("preview", info.configured ? "可生成示例语音试听" : "生成示例语音并试听");
}
function markTtsStep(step, text, active = false) {
  const item = $("edit-tts-steps")?.querySelector(`[data-step="${step}"]`);
  if (!item) return;
  item.textContent = text; item.classList.toggle("is-active", active); item.classList.toggle("is-complete", !active && text.includes("已"));
}
function syncEditTtsControls() { $("edit-tts-auto-play").disabled = !$("edit-tts-enabled").checked; }
function syncEditTtsPreview(referenceConfigured) {
  $("edit-tts-generate-preview").disabled = !referenceConfigured;
  $("edit-tts-open-settings").classList.toggle("is-hidden", state.ttsConfigured);
}
function previewSelectedReference() {
  const files = [...$("edit-tts-reference").files];
  if (!files.length) return setText("edit-tts-reference-status", "未选择参考音频");
  const invalid = files.find((file) => !file.name.toLowerCase().endsWith(".wav") || file.size > 10 * 1024 * 1024);
  if (invalid) return setText("edit-tts-reference-status", `文件不可用：${invalid.name}（仅支持 10 MB 内 WAV）`);
  setText("edit-tts-reference-status", `已选择 ${files.length} 条，点击“确认上传音色”开始处理`);
  $("edit-tts-confirm-upload").disabled = false;
  markTtsStep("select", `已选择 ${files.length} 条 WAV`, true);
}
async function confirmReferenceUpload() {
  if (!state.editPersona) return;
  const files = [...$("edit-tts-reference").files];
  if (!files.length) return setText("edit-tts-reference-status", "请先选择 WAV 音频");
  const invalid = files.find((file) => !file.name.toLowerCase().endsWith(".wav") || file.size > 10 * 1024 * 1024);
  if (invalid) return setText("edit-tts-reference-status", `文件不可用：${invalid.name}（仅支持 10 MB 内 WAV）`);
  const button = $("edit-tts-confirm-upload"); button.disabled = true;
  markTtsStep("upload", `正在校验并合并 ${files.length} 条音频…`, true); setText("edit-tts-reference-status", "正在校验音频格式与大小…");
  try {
    const form = new FormData(); files.forEach((file) => form.append("files", file));
    setText("edit-tts-reference-status", `正在上传并处理 ${files.length} 条音频…`);
    state.editPersona = await api(fetch(`/api/tts/personas/${state.editPersona.id}/reference`, { method: "POST", headers: { "X-PersonaLive-Request": "web" }, body: form }));
    $("edit-tts-reference").value = "";
    setText("edit-tts-reference-status", "音色处理完成，正在刷新试听状态…"); markTtsStep("upload", "音色文件已处理"); markTtsStep("preview", "可生成示例语音试听", true);
    await loadEditReference(); setText("edit-persona-status", "参考音色已保存，可生成示例语音试听");
  } catch (reason) { setText("edit-tts-reference-status", reason); markTtsStep("upload", "上传处理失败"); }
  finally { button.disabled = false; }
}
async function playEditReference() {
  if (!state.editPersona) return;
  try {
    const response = await fetch(`/api/tts/personas/${state.editPersona.id}/reference/audio`, { headers: { "X-PersonaLive-Request": "web" } });
    if (!response.ok) throw new Error("参考音色尚未配置");
    if (state.editReferenceUrl) URL.revokeObjectURL(state.editReferenceUrl);
    state.editReferenceUrl = URL.createObjectURL(await response.blob());
    const audio = new Audio(state.editReferenceUrl); audio.play();
  } catch (reason) { setText("edit-persona-status", reason); }
}
async function removeEditReference() {
  if (!state.editPersona || !window.confirm("移除当前角色的参考音色？")) return;
  try {
    await api(fetch(`/api/tts/personas/${state.editPersona.id}/reference`, { method: "DELETE", headers: { "X-PersonaLive-Request": "web" } }));
    state.editPersona.profile = { ...(state.editPersona.profile || {}), tts: { ...(state.editPersona.profile?.tts || {}) } };
    delete state.editPersona.profile.tts.reference_audio;
    setText("edit-persona-status", "参考音色已移除"); await loadEditReference();
  } catch (reason) { setText("edit-persona-status", reason); }
}
async function generateEditPreview() {
  if (!state.editPersona) return;
  if (!state.ttsConfigured) return openTtsSettings();
  const text = $("edit-tts-preview-text").value.trim();
  if (!text) return setText("edit-tts-preview-status", "请输入示例文案");
  const button = $("edit-tts-generate-preview"); button.disabled = true; setText("edit-tts-preview-status", "正在生成示例语音…");
  try {
    const response = await fetch(`/api/tts/personas/${state.editPersona.id}/reference/preview`, { method: "POST", headers: { "Content-Type": "application/json", "X-PersonaLive-Request": "web" }, body: JSON.stringify({ text }) });
    if (!response.ok) { const data = await response.json().catch(() => null); if (response.status === 409) { setText("edit-tts-preview-status", "请先安装 TTS 模型"); return openTtsSettings(); } throw new Error(data?.detail || `请求失败 (${response.status})`); }
    const audio = $("edit-tts-preview-audio"); if (audio.src) URL.revokeObjectURL(audio.src); audio.src = URL.createObjectURL(await response.blob()); audio.classList.remove("is-hidden"); setText("edit-tts-preview-status", "示例语音已生成"); audio.play().catch(() => {});
  } catch (reason) { setText("edit-tts-preview-status", reason.message || reason); }
  finally { await loadEditReference(); }
}
function openTtsSettings() { switchView("settings"); const section = $("tts-settings-anchor"); section.open = true; section.scrollIntoView({ behavior: "smooth", block: "start" }); }
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
  const submit = $("edit-persona-form").querySelector("button[type=submit]"); submit.disabled = true; setText("edit-persona-status", "正在保存角色资料设置…");
  try {
    const referenceFiles = [...$("edit-tts-reference").files];
    if (referenceFiles.length) throw new Error("已选择新的音频，请先点击“确认上传音色”再保存角色资料");
    const profile = { ...(state.editPersona.profile || {}), description: $("edit-persona-profile").value.trim(), tts: { ...(state.editPersona.profile?.tts || {}), enabled: $("edit-tts-enabled").checked, auto_play: $("edit-tts-auto-play").checked } };
    state.editPersona = await api(fetch(`/api/personas/${state.editPersona.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: $("edit-persona-name").value.trim(), profile }) }));
    setText("edit-persona-status", "角色资料已保存"); await loadPersonas(); $("edit-persona-select").value = state.editPersona.id; await loadEditReference();
  } catch (reason) { setText("edit-persona-status", reason); }
  finally { submit.disabled = false; }
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
  if (state.realtimeTurnId || state.realtimeExecutionPending || state.audioStarting || state.audioMode !== "idle") return;
  const question = $("question").value.trim(); if (!question) return;
  state.agentRequestPending = true;
  appendMessage("user", question); resetChatProcess(); showReplyLoading(); setText("chat-error"); updateComposerControls();
  if (sendRealtime({ type: "text.submit", question })) {
    awaitRealtimeAcknowledgement(question);
    $("question-form").reset(); resizeComposer();
    return;
  }
  try {
    const result = await api(fetch(`/api/personas/${state.activePersona.id}/agent/query`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question, conversation_id: state.conversationId }) }));
    handleAgentResult(result); $("question-form").reset(); resizeComposer();
  } catch (reason) { setText("chat-error", reason); }
  finally { state.agentRequestPending = false; updateComposerControls(); }
}
function resizeComposer() {
  const input = $("question");
  input.style.height = "40px";
  const height = Math.min(input.scrollHeight, 104);
  input.style.height = `${height}px`;
  input.style.overflowY = input.scrollHeight > 104 ? "auto" : "hidden";
}
function appendMessage(type, text) {
  if ($("chat-log").querySelector(".empty-state")) $("chat-log").replaceChildren();
  const node = document.createElement("article"); node.className = `message message-${type}`;
  const body = document.createElement("p"); body.textContent = text; node.append(body); $("chat-log").append(node); node.scrollIntoView({ block: "nearest" }); return node;
}
function appendVoiceControl(node, audio) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "voice-play-button";
  button.title = "播放语音";
  button.setAttribute("aria-label", "播放语音");
  const icon = document.createElement("i");
  icon.dataset.lucide = "volume-2";
  button.append(icon);
  button.addEventListener("click", () => {
    if (audio.paused) audio.play().catch(() => {});
    else audio.pause();
  });
  audio.addEventListener("play", () => button.classList.add("is-playing"));
  audio.addEventListener("pause", () => button.classList.remove("is-playing"));
  audio.addEventListener("ended", () => button.classList.remove("is-playing"));
  node.append(button, audio);
  icons();
  return button;
}
function appendAudioMessage(message) {
  if ($("chat-log").querySelector(".empty-state")) $("chat-log").replaceChildren();
  const node = document.createElement("article");
  node.className = `message message-${message.role} message-audio`; node.dataset.messageId = message.id;
  const audio = document.createElement("audio"); audio.controls = false; audio.preload = "metadata"; audio.src = message.audio_url; audio.className = "voice-audio-source";
  if (message.role === "assistant") {
    const body = document.createElement("p"); body.textContent = message.content; const status = document.createElement("span"); status.className = "voice-bubble-status"; status.textContent = "语音回复"; audio.controls = false; audio.className = "voice-audio-source"; node.append(body, status); appendVoiceControl(node, audio);
    $("chat-log").append(node); node.scrollIntoView({ block: "nearest" }); return node;
  }
  const voiceLabel = document.createElement("span"); voiceLabel.className = "voice-bubble-label"; voiceLabel.textContent = "语音消息";
  node.append(voiceLabel); appendVoiceControl(node, audio);
  const transcript = document.createElement("details"); transcript.className = "voice-transcript";
  const summary = document.createElement("summary"); summary.textContent = message.status === "failed" ? "识别失败" : "查看转写";
  const text = document.createElement("p"); text.textContent = message.transcript || (message.status === "failed" ? message.error_message : "正在识别…");
  transcript.append(summary, text); node.append(audio, transcript);
  if (message.status === "failed") {
    const retry = document.createElement("button"); retry.type = "button"; retry.className = "voice-retry"; retry.textContent = "重试";
    retry.addEventListener("click", () => retryVoiceMessage(message.id)); node.append(retry);
  }
  $("chat-log").append(node); node.scrollIntoView({ block: "nearest" }); return node;
}
function updateAudioMessage(message) {
  const current = $("chat-log").querySelector(`[data-message-id="${message.id}"]`);
  if (current) current.remove();
  appendAudioMessage(message);
}
async function retryVoiceMessage(messageId) {
  try {
    const result = await api(fetch(`/api/voice-messages/${messageId}/transcribe`, { method: "POST", headers: { "X-PersonaLive-Request": "web" } }));
    updateAudioMessage(result.message); handleAgentResult(result.turn);
  } catch (reason) { setText("chat-error", reason); await loadConversationMessages(); }
}
async function loadConversationMessages() {
  if (!state.activePersona) return;
  try {
    const messages = await api(fetch(`/api/personas/${state.activePersona.id}/conversations/${state.conversationId}/messages`));
    $("chat-log").replaceChildren();
    if (!messages.length) return $("chat-log").append(empty("开始对话"));
    for (const message of messages) message.kind === "audio" ? appendAudioMessage(message) : appendMessage(message.role, message.content);
  } catch (reason) { setText("chat-error", reason); }
}
async function clearConversation() {
  if (!state.activePersona || !confirm("永久删除当前对话、转写和音频？")) return;
  try {
    await api(fetch(`/api/personas/${state.activePersona.id}/conversations/${state.conversationId}`, { method: "DELETE", headers: { "X-PersonaLive-Request": "web" } }));
    closeRealtime();
    state.conversationId = crypto.randomUUID();
    localStorage.setItem(`personalive:conversation:${state.activePersona.id}`, state.conversationId);
    $("chat-log").replaceChildren(empty("开始对话")); connectRealtime();
  } catch (reason) { setText("chat-error", reason); }
}
function handleAgentResult(result) {
  state.pendingAction = result.status === "pending_confirmation" ? { action: result.pending_action, specialist: result.specialist } : null;
  renderConfirmation(); $("send-question").disabled = Boolean(state.pendingAction) || !state.activePersona;
  if (result.answer) {
    const node = replaceReplyLoading(state.pendingReplyNode, result.answer);
    appendResultDetails(node, result);
    renderChatProcess(result);
    synthesizeAnswer(result.answer, node);
  } else if (state.pendingReplyNode) { state.pendingReplyNode.remove(); state.pendingReplyNode = null; }
}
function appendAnswer(result) { const node = replaceReplyLoading(state.pendingReplyNode, result.answer); renderChatProcess(result); synthesizeAnswer(result.answer, node); }
function collectStreamVoice(text, node) {
  if (!state.activePersona?.profile?.tts?.enabled || !$("assistant-voice-toggle").checked) return;
  state.voiceStreamBuffer += text;
}
function flushStreamVoice(force, node) {
  if (!state.voiceStreamBuffer) return;
  if (!force && state.voiceStreamBuffer.length < 60) return;
  const text = state.voiceStreamBuffer.trim(); state.voiceStreamBuffer = "";
  if (text) synthesizeAnswer(text, node, { queued: true });
}
function enqueueVoiceAudio(audio) {
  state.voicePlaybackQueue.push(audio);
  playNextVoiceAudio();
}
function playNextVoiceAudio() {
  if (state.voicePlaybackActive || !state.voicePlaybackQueue.length) return;
  state.voicePlaybackActive = true;
  const audio = state.voicePlaybackQueue.shift();
  audio.addEventListener("ended", () => { state.voicePlaybackActive = false; playNextVoiceAudio(); }, { once: true });
  audio.play().catch(() => { state.voicePlaybackActive = false; playNextVoiceAudio(); });
}
async function synthesizeAnswer(text, node, options = {}) {
  const voice = state.activePersona?.profile?.tts;
  if (!state.ttsConfigured || !voice?.enabled || !$("assistant-voice-toggle").checked || !text) return;
  const status = document.createElement("span"); status.className = "voice-bubble-status is-generating"; status.textContent = "正在生成语音…"; node.append(status);
  try {
    const message = await api(fetch(`/api/tts/personas/${state.activePersona.id}/conversations/${state.conversationId}/synthesize`, { method: "POST", headers: { "Content-Type": "application/json", "X-PersonaLive-Request": "web" }, body: JSON.stringify({ text }) }));
    status.textContent = "语音已生成"; status.classList.remove("is-generating");
    const audio = document.createElement("audio"); audio.controls = false; audio.preload = "metadata"; audio.src = message.audio_url; audio.className = "voice-audio-source"; appendVoiceControl(node, audio);
    if (voice.auto_play !== false) options.queued ? enqueueVoiceAudio(audio) : audio.play().catch(() => {});
  } catch (reason) { status.textContent = "语音生成失败"; status.classList.remove("is-generating"); setText("chat-error", `文字回复正常，语音生成失败：${reason.message || reason}`); }
}
function appendResultDetails(node, result) { if (result.evidence?.length) node.append(details("引用", result.evidence)); }
function renderConfirmation() {
  $("confirmation-panel").classList.toggle("is-hidden", !state.pendingAction); if (!state.pendingAction) return;
  const action = state.pendingAction.action || {}; $("confirmation-title").textContent = action.title || "确认操作"; $("confirmation-detail").textContent = `${action.target || "当前角色"} · ${JSON.stringify(action.arguments || {})}`;
}
async function resumeAgent(approved) {
  if (!state.pendingAction || !state.activePersona) return;
  $("confirm-action").disabled = true; $("cancel-action").disabled = true;
  if (sendRealtime({ type: "confirmation.respond", specialist: state.pendingAction.specialist, approved })) {
    awaitRealtimeAcknowledgement("");
    return;
  }
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
    state.openaiKeyConfigured = config.openai_api_key_configured;
    state.embeddingKeyConfigured = config.embedding_api_key_configured;
    state.webSearchKeyConfigured = config.web_search_api_key_configured;
    $("openai-base-url").value = config.openai_base_url; $("openai-model").value = config.openai_model;
    $("embedding-base-url").value = config.embedding_base_url; $("embedding-model").value = config.embedding_model;
    $("web-search-base-url").value = config.web_search_base_url;
    $("llm-provider").value = inferProvider(LLM_PRESETS, config.openai_base_url);
    $("embedding-provider").value = config.embedding_provider;
    $("embedding-model-source").value = config.embedding_model_source;
    $("embedding-device").value = config.embedding_device;
    $("embedding-dimensions").value = config.embedding_dimensions;
    $("embedding-send-dimensions").checked = config.embedding_send_dimensions;
    $("chunk-size").value = config.chunk_size;
    $("chunk-overlap").value = config.chunk_overlap;
    $("web-search-enabled").checked = config.enable_web_fallback;
    $("web-search-provider").value = config.web_search_provider === "off" ? "bocha" : config.web_search_provider;
    state.savedEmbeddingDimensions = config.embedding_dimensions;
    syncManagedEmbeddingPreset(); renderEmbeddingSettings(); renderEmbeddingInstallAction(); renderEmbeddingWarning(); renderWebSearchSettings(); renderAudioState();
  } catch (reason) { setText("settings-status", reason); }
}
async function loadEmbeddingStatus() {
  try {
    const config = await api(fetch("/api/embedding/status"));
    const wasReady = state.embeddingConfigured;
    state.embeddingConfigured = config.ready;
    state.embeddingInstalledModel = config.installed ? config.model_id : "";
    state.embeddingResourceStatus = config;
    const embeddingState = config.installing ? "installing" : config.ready ? "ready" : "not_installed";
    const phaseNames = { preparing: "准备安装", runtime: "安装运行环境", model: "下载模型", loading: "加载并探测维度", cancelling: "正在取消", complete: "已就绪", error: "安装失败" };
    $("embedding-state").textContent = config.installing ? (phaseNames[config.phase] || "处理中") : config.ready ? "已就绪" : "尚未安装";
    const device = config.actual_device ? ` · ${config.actual_device.toUpperCase()}` : "";
    setText("embedding-status", config.error || (config.ready ? `${config.model_id} · ${config.dimensions} 维${device} · ${config.model_dir}` : `${config.model_id} · ${config.source === "modelscope" ? "ModelScope" : "Hugging Face"}`));
    const progress = $("embedding-progress");
    progress.classList.toggle("is-hidden", !config.installing);
    if (config.progress_percent == null) progress.removeAttribute("value"); else progress.value = config.progress_percent;
    setText("embedding-progress-detail", config.installing ? `${phaseNames[config.phase] || "处理中"}${config.elapsed_seconds ? ` · 已用时 ${config.elapsed_seconds} 秒` : ""}` : "");
    renderEmbeddingInstallAction();
    $("cancel-embedding").classList.toggle("is-hidden", !config.installing);
    $("cancel-embedding").disabled = !config.installing || config.cancelling;
    $("remove-embedding").disabled = config.installing || !config.installed;
    $("open-embedding-directory").disabled = config.installing;
    renderServiceStatus("embedding", "Embedding", embeddingState, embeddingState);
    if (config.ready && !wasReady && Number($("embedding-dimensions").value) !== Number(config.dimensions)) await loadSettings();
    if (config.installing) setTimeout(loadEmbeddingStatus, 2000);
  } catch (reason) {
    state.embeddingConfigured = false;
    setText("embedding-status", reason);
  }
}
function embeddingResourcePayload() {
  return { model_id: $("embedding-model").value.trim(), source: $("embedding-model-source").value, device: $("embedding-device").value };
}
async function installEmbedding() {
  if (!validateSettings()) return;
  if (!confirm("将自动安装独立运行环境并下载所选 Embedding 模型，是否继续？")) return;
  $("install-embedding").disabled = true;
  $("install-embedding").textContent = "安装中";
  try {
    await api(fetch("/api/embedding/install", { method: "POST", headers: { "Content-Type": "application/json", "X-PersonaLive-Request": "web" }, body: JSON.stringify(embeddingResourcePayload()) }));
    await loadEmbeddingStatus();
  } catch (reason) { setText("embedding-status", reason); $("install-embedding").disabled = false; }
}
async function cancelEmbedding() {
  $("cancel-embedding").disabled = true;
  try {
    await api(fetch("/api/embedding/install/cancel", { method: "DELETE", headers: { "X-PersonaLive-Request": "web" } }));
    await loadEmbeddingStatus();
  } catch (reason) { setText("embedding-status", reason); $("cancel-embedding").disabled = false; }
}
async function removeEmbedding() {
  if (!confirm("删除当前本地 Embedding 模型？Milvus 中的资料不会被删除。")) return;
  $("remove-embedding").disabled = true;
  try {
    await api(fetch("/api/embedding/model", { method: "DELETE", headers: { "X-PersonaLive-Request": "web" } }));
    await loadEmbeddingStatus();
  } catch (reason) { setText("embedding-status", reason); $("remove-embedding").disabled = false; }
}
async function openEmbeddingDirectory() {
  $("open-embedding-directory").disabled = true;
  try {
    const result = await api(fetch("/api/embedding/model-directory", { method: "POST", headers: { "X-PersonaLive-Request": "web" } }));
    setText("embedding-status", `已打开：${result.opened_directory}`);
  } catch (reason) { setText("embedding-status", reason); }
  finally { $("open-embedding-directory").disabled = false; }
}
async function loadAsrStatus() {
  try {
    const config = await api(fetch("/api/asr/status"));
    $("asr-enabled").checked = config.enabled;
    $("asr-python-path").value = config.python_path || "";
    $("asr-model-path").value = config.model_path || "";
    $("asr-ffmpeg-path").value = config.ffmpeg_path || "";
    state.asrConfigured = config.ready;
    const asrState = config.installing ? "installing" : config.ready ? "ready" : config.enabled ? "not_installed" : "disabled";
    $("asr-state").textContent = { installing: "正在安装", ready: "已就绪", not_installed: "尚未安装", disabled: "已关闭" }[asrState];
    renderServiceStatus("asr", "ASR", asrState, asrState);
    setText("asr-status", config.error || (config.ready ? `Qwen3-ASR-0.6B · ${config.resolved_model}` : config.download_size));
    const phaseNames = { preparing: "准备安装", runtime: "安装运行环境", model: "从 ModelScope 下载模型", ffmpeg: "准备 FFmpeg", cancelling: "正在取消", complete: "已就绪", error: "安装失败" };
    const progress = $("asr-progress");
    progress.classList.toggle("is-hidden", !config.installing);
    if (config.progress_percent == null) progress.removeAttribute("value"); else progress.value = config.progress_percent;
    setText("asr-progress-detail", config.installing ? `${phaseNames[config.phase] || "处理中"}${config.elapsed_seconds ? ` · 已用时 ${config.elapsed_seconds} 秒` : ""}` : "");
    $("install-asr").disabled = config.installing || config.installed;
    $("install-asr").textContent = config.installing ? "安装中" : config.installed ? "已安装" : "自动下载安装";
    $("cancel-asr").classList.toggle("is-hidden", !config.installing);
    $("cancel-asr").disabled = !config.installing || config.cancelling;
    $("remove-asr").disabled = config.installing || !config.managed_installed;
    updateComposerControls();
    if (config.installing) setTimeout(loadAsrStatus, 2000);
  } catch (reason) {
    state.asrConfigured = false; renderServiceStatus("asr", "ASR", "unavailable"); setText("asr-status", reason); updateComposerControls();
  }
}
async function saveAsrConfig() {
  $("save-asr").disabled = true;
  try {
    await api(fetch("/api/asr/config", { method: "PATCH", headers: { "Content-Type": "application/json", "X-PersonaLive-Request": "web" }, body: JSON.stringify({ enabled: $("asr-enabled").checked, python_path: $("asr-python-path").value.trim(), model_path: $("asr-model-path").value.trim(), ffmpeg_path: $("asr-ffmpeg-path").value.trim() }) }));
    await loadAsrStatus();
  } catch (reason) { setText("asr-status", reason); }
  finally { $("save-asr").disabled = false; }
}
async function installAsr() {
  if (!confirm("将下载约 5-10 GB 的 CUDA 运行环境和 Qwen3-ASR 模型，是否继续？")) return;
  $("install-asr").disabled = true;
  $("install-asr").textContent = "安装中";
  try {
    await saveAsrConfig();
    await api(fetch("/api/asr/install", { method: "POST", headers: { "X-PersonaLive-Request": "web" } }));
    await loadAsrStatus();
  } catch (reason) { setText("asr-status", reason); $("install-asr").disabled = false; }
}
async function removeAsr() {
  if (!confirm("删除项目自动下载的 ASR 环境和模型？外部目录不会被删除。")) return;
  $("remove-asr").disabled = true;
  try {
    await api(fetch("/api/asr/install", { method: "DELETE", headers: { "X-PersonaLive-Request": "web" } }));
    await loadAsrStatus();
  } catch (reason) { setText("asr-status", reason); $("remove-asr").disabled = false; }
}
async function loadTtsStatus() {
  try {
    const config = await api(fetch("/api/tts/status"));
    state.ttsConfigured = config.ready;
    $("tts-enabled").checked = config.enabled;
    $("tts-use-gpu").checked = config.use_gpu;
    $("tts-use-gpu").disabled = config.installing;
    const ttsState = config.installing ? "installing" : config.ready ? "ready" : config.enabled ? "not_installed" : "disabled";
    renderServiceStatus("tts", "TTS", ttsState, ttsState);
    const phaseNames = { preparing: "准备下载", model: "下载语音模型", cancelling: "正在取消", complete: "安装完成", error: "安装失败" };
    $("tts-state").textContent = config.installing ? (phaseNames[config.phase] || "正在安装") : config.ready ? "已就绪" : config.enabled ? "尚未安装" : "已关闭";
    setText("tts-status", config.error || (!config.runtime_bundled ? "当前开发目录缺少内置 Lunar TTS 运行库；完整 Windows 发布包将自带该文件" : config.ready ? `Qwen3-TTS-0.6B · ${config.model_dir}` : config.download_size));
    const progress = $("tts-progress"); progress.classList.toggle("is-hidden", !config.installing);
    if (config.progress_percent == null) progress.removeAttribute("value"); else progress.value = config.progress_percent;
    const size = (bytes) => bytes ? `${(bytes / 1024 / 1024).toFixed(bytes > 1024 * 1024 * 100 ? 0 : 1)} MB` : "";
    const duration = (seconds) => seconds == null ? "正在估算剩余时间" : seconds < 60 ? `预计剩余 ${seconds} 秒` : `预计剩余 ${Math.ceil(seconds / 60)} 分钟`;
    const speed = config.download_speed_bytes ? `${size(config.download_speed_bytes)}/s` : "";
    setText("tts-progress-detail", config.installing ? [config.current_file, size(config.downloaded_bytes), config.total_bytes ? `/ ${size(config.total_bytes)}` : "", speed, duration(config.eta_seconds)].filter(Boolean).join(" · ") : "");
    $("install-tts").disabled = config.installing || config.installed || !config.runtime_bundled;
    $("install-tts").textContent = config.installing ? "安装中" : config.installed ? "已安装" : "自动下载安装";
    $("cancel-tts").classList.toggle("is-hidden", !config.installing);
    $("cancel-tts").disabled = !config.installing || config.cancelling;
    $("remove-tts").disabled = config.installing || !config.model_dir;
    $("open-tts-directory").disabled = config.installing;
    $("preview-tts").disabled = !config.ready;
    if (state.editPersona) {
      const reference = await api(fetch(`/api/tts/personas/${state.editPersona.id}/reference`, { headers: { "X-PersonaLive-Request": "web" } }));
      syncEditTtsPreview(reference.configured);
    }
    if (config.installing) setTimeout(loadTtsStatus, 2000);
  } catch (reason) { state.ttsConfigured = false; renderServiceStatus("tts", "TTS", "unavailable"); setText("tts-status", reason); }
}
async function cancelAsr() {
  $("cancel-asr").disabled = true;
  try {
    await api(fetch("/api/asr/install/cancel", { method: "DELETE", headers: { "X-PersonaLive-Request": "web" } }));
    await loadAsrStatus();
  } catch (reason) { setText("asr-status", reason); $("cancel-asr").disabled = false; }
}
async function openAsrDirectory() {
  const button = $("open-asr-directory");
  button.disabled = true;
  try {
    const result = await api(fetch("/api/asr/model-directory", { method: "POST", headers: { "X-PersonaLive-Request": "web" } }));
    setText("asr-status", `已打开：${result.opened_directory}`);
  } catch (reason) { setText("asr-status", reason); }
  finally { button.disabled = false; }
}
async function saveTtsConfig() {
  try {
    await api(fetch("/api/tts/config", { method: "PATCH", headers: { "Content-Type": "application/json", "X-PersonaLive-Request": "web" }, body: JSON.stringify({ enabled: $("tts-enabled").checked, use_gpu: $("tts-use-gpu").checked }) }));
    await loadTtsStatus();
  } catch (reason) { setText("tts-status", reason); }
}
async function installTts() {
  if (!confirm("将下载约 3 GB 的 Qwen3-TTS GGUF 模型，Lunar TTS 运行库已随应用内置。是否继续？")) return;
  $("install-tts").disabled = true;
  $("install-tts").textContent = "安装中";
  try {
    await api(fetch("/api/tts/install", { method: "POST", headers: { "X-PersonaLive-Request": "web" } }));
    await loadTtsStatus();
  } catch (reason) { setText("tts-status", reason); $("install-tts").disabled = false; }
}
async function removeTts() {
  if (!confirm("删除已下载的 TTS 模型？内置运行库和角色参考声音不会删除。")) return;
  $("remove-tts").disabled = true;
  try {
    await api(fetch("/api/tts/install", { method: "DELETE", headers: { "X-PersonaLive-Request": "web" } }));
    await loadTtsStatus();
  } catch (reason) { setText("tts-status", reason); $("remove-tts").disabled = false; }
}
async function cancelTts() {
  $("cancel-tts").disabled = true;
  try {
    await api(fetch("/api/tts/install/cancel", { method: "DELETE", headers: { "X-PersonaLive-Request": "web" } }));
    await loadTtsStatus();
  } catch (reason) { setText("tts-status", reason); $("cancel-tts").disabled = false; }
}
async function openTtsDirectory() {
  $("open-tts-directory").disabled = true;
  try {
    const result = await api(fetch("/api/tts/model-directory", { method: "POST", headers: { "X-PersonaLive-Request": "web" } }));
    setText("tts-status", `已打开：${result.opened_directory}`);
  } catch (reason) { setText("tts-status", reason); }
  finally { $("open-tts-directory").disabled = false; }
}
async function previewTts() {
  const text = $("tts-preview-text").value.trim();
  if (!text) return setText("tts-preview-status", "请输入试听文本");
  const button = $("preview-tts");
  button.disabled = true;
  setText("tts-preview-status", "正在生成试听");
  try {
    const response = await fetch("/api/tts/preview", { method: "POST", headers: { "Content-Type": "application/json", "X-PersonaLive-Request": "web" }, body: JSON.stringify({ text }) });
    if (!response.ok) {
      const data = await response.json().catch(() => null);
      throw new Error(data?.detail || `请求失败 (${response.status})`);
    }
    const audio = $("tts-preview-audio");
    if (audio.src) URL.revokeObjectURL(audio.src);
    audio.src = URL.createObjectURL(await response.blob());
    audio.classList.remove("is-hidden");
    setText("tts-preview-status", "试听已生成");
    audio.play().catch(() => {});
  } catch (reason) { setText("tts-preview-status", reason.message || reason); }
  finally { button.disabled = !state.ttsConfigured; }
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
  const provider = $("embedding-provider").value;
  const preset = EMBEDDING_PRESETS[provider];
  if (preset) {
    $("embedding-base-url").value = preset.baseUrl; $("embedding-model").value = preset.model;
    $("embedding-dimensions").value = preset.dimensions; $("embedding-send-dimensions").checked = preset.sendDimensions;
  }
  syncManagedEmbeddingPreset(); renderEmbeddingSettings(); markEmbeddingSelectionChanged(); renderEmbeddingWarning();
}
function applyManagedEmbeddingPreset() {
  const selected = $("managed-embedding-preset").value;
  if (selected !== "custom") $("embedding-model").value = selected;
  markEmbeddingSelectionChanged();
}
function markEmbeddingSelectionChanged() {
  syncManagedEmbeddingPreset();
  renderEmbeddingInstallAction();
  if ($("embedding-provider").value === "managed_local" && $("embedding-model").value.trim() !== state.embeddingInstalledModel) {
    setText("embedding-status", "选择新模型后，点击“下载并启用”。");
  }
}
function renderEmbeddingInstallAction() {
  const button = $("install-embedding");
  const status = state.embeddingResourceStatus;
  const managed = $("embedding-provider").value === "managed_local";
  const selectedModel = $("embedding-model").value.trim();
  const installed = Boolean(status?.installed && status.model_id === selectedModel);
  if (status?.installing) {
    button.textContent = "安装中";
    button.disabled = true;
  } else if (managed && installed) {
    button.textContent = "已安装";
    button.disabled = true;
  } else {
    button.textContent = "下载并启用";
    button.disabled = !managed || !selectedModel;
  }
}
function syncManagedEmbeddingPreset() {
  const model = $("embedding-model").value.trim();
  const options = [...$("managed-embedding-preset").options].map((option) => option.value);
  $("managed-embedding-preset").value = options.includes(model) ? model : "custom";
}
function renderEmbeddingSettings() {
  const managed = $("embedding-provider").value === "managed_local";
  $("managed-embedding-fields").classList.toggle("is-hidden", !managed);
  $("embedding-api-key-field").classList.toggle("is-hidden", managed);
  $("embedding-base-url-field").classList.toggle("is-hidden", managed);
  $("embedding-send-dimensions-field").classList.toggle("is-hidden", managed);
  $("embedding-dimensions").readOnly = managed;
  if (managed) $("embedding-send-dimensions").checked = false;
}
function renderEmbeddingWarning() {
  const changed = Number($("embedding-dimensions").value) !== Number(state.savedEmbeddingDimensions);
  const warning = $("embedding-dimension-warning");
  warning.textContent = changed ? "向量维度已改变。保存后请使用匹配维度的 Milvus Collection，并重新入库已有资料。" : "";
  warning.classList.toggle("is-hidden", !changed);
  const chunkSize = Number($("chunk-size").value);
  const chunkOverlap = Number($("chunk-overlap").value);
  const chunkWarning = $("chunk-settings-warning");
  const invalidChunk = chunkSize && chunkOverlap > Math.floor(chunkSize / 4);
  chunkWarning.textContent = invalidChunk ? "重叠长度不能超过切分长度的 25%。" : "";
  chunkWarning.classList.toggle("is-hidden", !invalidChunk);
}
function renderWebSearchSettings() {
  const enabled = $("web-search-enabled").checked;
  const provider = $("web-search-provider").value;
  const isCustom = provider === "custom";
  $("web-search-provider").disabled = !enabled;
  $("web-search-api-key").disabled = !enabled;
  $("web-search-base-url-field").classList.toggle("is-hidden", !isCustom);
  $("web-search-base-url").disabled = !enabled || !isCustom;
  const guide = WEB_SEARCH_GUIDES[provider] || WEB_SEARCH_GUIDES.off;
  const text = document.createElement("p"); text.textContent = guide.text;
  $("web-search-guide").replaceChildren(text);
  if (guide.href) {
    const link = document.createElement("a"); link.href = guide.href; link.target = "_blank"; link.rel = "noopener"; link.textContent = guide.link;
    const source = document.createElement("p"); source.textContent = `${guide.label}：`; source.append(link); $("web-search-guide").append(source);
  }
  const missingKey = enabled && !state.webSearchKeyConfigured && !$("web-search-api-key").value.trim();
  const invalidUrl = enabled && isCustom && !isHttpUrl($("web-search-base-url").value);
  const warning = $("web-search-warning");
  warning.textContent = missingKey ? "启用联网搜索后需要填写 API Key。" : invalidUrl ? "自定义搜索需要填写完整的 HTTP(S) 接口地址。" : "";
  warning.classList.toggle("is-hidden", !warning.textContent);
}
function requestSettingsSave(event) { event.preventDefault(); openSettingsConfirmation("save"); }
function requestSettingsReset() { openSettingsConfirmation("reset"); }
function openSettingsConfirmation(action) {
  if (action === "save" && !validateSettings()) return;
  state.settingsAction = action;
  const isSave = action === "save";
  $("settings-confirm-title").textContent = isSave ? "保存前确认" : "确认重置配置";
  $("settings-confirm-detail").textContent = isSave
    ? buildConfigDetail()
    : "将清除本机前端保存的 LLM、Embedding、联网搜索配置和 Key。不会影响 .env 中的 MySQL、Milvus 或端口配置。";
  $("settings-confirm-submit").textContent = isSave ? "确认保存" : "确认重置";
  $("settings-confirm-dialog").showModal();
}
function isHttpUrl(value) {
  try { return ["http:", "https:"].includes(new URL(value).protocol); }
  catch { return false; }
}
function validateSettings() {
  const managedEmbedding = $("embedding-provider").value === "managed_local";
  const modelId = $("embedding-model").value.trim();
  const localModelInvalid = managedEmbedding && (!/^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(modelId) || modelId.split("/").includes(".."));
  const embeddingMissing = !modelId || !Number($("embedding-dimensions").value) || (!managedEmbedding && (!$("embedding-base-url").value.trim() || (!state.embeddingKeyConfigured && !$("embedding-api-key").value.trim())));
  const chunkSize = Number($("chunk-size").value);
  const chunkOverlap = Number($("chunk-overlap").value);
  const chunkInvalid = chunkSize < 200 || chunkSize > 4000 || chunkOverlap < 0 || chunkOverlap > 1000 || chunkOverlap > Math.floor(chunkSize / 4);
  const webEnabled = $("web-search-enabled").checked;
  const webInvalid = webEnabled && ((!state.webSearchKeyConfigured && !$("web-search-api-key").value.trim()) || ($("web-search-provider").value === "custom" && !isHttpUrl($("web-search-base-url").value)));
  renderEmbeddingWarning(); renderWebSearchSettings();
  if (localModelInvalid) setText("settings-status", "自定义模型 ID 格式不正确，请参考 Qwen/Qwen3-Embedding-0.6B。");
  else if (embeddingMissing) setText("settings-status", managedEmbedding ? "请选择或填写本地 Embedding 模型。" : "请完整填写 Embedding 的 Base URL、模型、维度和 API Key。");
  else if (chunkInvalid) setText("settings-status", "请检查文档切分参数：重叠长度不能超过切分长度的 25%。");
  else if (webInvalid) setText("settings-status", "请补全联网搜索的 API Key 和兼容接口地址。");
  else return true;
  return false;
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
    const webEnabled = $("web-search-enabled").checked;
    await api(fetch("/api/settings", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ openai_api_key: value("openai-api-key"), openai_base_url: value("openai-base-url"), openai_model: value("openai-model"), embedding_api_key: value("embedding-api-key"), embedding_provider: $("embedding-provider").value, embedding_model_source: $("embedding-model-source").value, embedding_device: $("embedding-device").value, embedding_base_url: value("embedding-base-url"), embedding_model: value("embedding-model"), embedding_dimensions: Number(value("embedding-dimensions")), embedding_send_dimensions: $("embedding-send-dimensions").checked, chunk_size: Number(value("chunk-size")), chunk_overlap: Number(value("chunk-overlap")), web_search_provider: webEnabled ? $("web-search-provider").value : "off", web_search_api_key: value("web-search-api-key"), web_search_base_url: value("web-search-base-url"), enable_web_fallback: webEnabled }) }));
    resetApiKeyInputs();
    setText("settings-status", "已保存，可立即使用"); await loadSettings();
  } catch (reason) { setText("settings-status", reason); }
  finally { $("save-settings").disabled = false; }
}
async function resetSettings() {
  $("reset-settings").disabled = true; setText("settings-status");
  try {
    await api(fetch("/api/settings", { method: "DELETE" }));
    resetApiKeyInputs(); $("web-search-base-url").value = "";
    setText("settings-status", "配置已重置"); await loadSettings();
  } catch (reason) { setText("settings-status", reason); }
  finally { $("reset-settings").disabled = false; }
}

function openPreview(item) { $("preview-title").textContent = item.original_filename; $("preview-content").textContent = item.markdown_preview || item.error_message || "暂无内容"; $("preview-drawer").classList.add("is-open"); $("preview-backdrop").classList.add("is-open"); }
function closePreview() { $("preview-drawer").classList.remove("is-open"); $("preview-backdrop").classList.remove("is-open"); }
function details(label, data) { const node = document.createElement("details"); const summary = document.createElement("summary"); summary.textContent = `${label} (${data.length})`; const pre = document.createElement("pre"); pre.textContent = JSON.stringify(data, null, 2); node.append(summary, pre); return node; }
function empty(text) { const node = document.createElement("p"); node.className = "empty-state"; node.textContent = text; return node; }
