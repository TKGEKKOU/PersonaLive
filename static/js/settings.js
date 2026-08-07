"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.settings = { init: initSettings };

function bindIf(id, event, handler) {
  const node = $(id);
  if (node) node.addEventListener(event, handler);
}
function setDisabled(id, value) {
  const node = $(id);
  if (node) node.disabled = value;
}
function setHidden(id, hidden) {
  const node = $(id);
  if (node) node.classList.toggle("is-hidden", hidden);
}
function friendlyError(reason) {
  const message = String(reason?.message || reason || "").trim();
  if (!message) return "请稍后重试";
  if (/null|undefined/.test(message)) return "页面组件未就绪，请刷新页面后重试";
  return message;
}

const GPT_SOVITS_PRESETS = [
  {
    id: "20250604-nvidia50",
    label: "v2Pro 20250604（nvidia50）",
    size: "约 8.8 GB",
    note: "当前内置版本 · 适配 NVIDIA 50 系，兼容常规 CUDA/CPU",
    url: "https://modelscope.cn/models/FlowerCry/gpt-sovits-7z-pacakges/resolve/master/GPT-SoVITS-v2pro-20250604-nvidia50.7z",
  },
  {
    id: "20250604",
    label: "v2Pro 20250604",
    size: "约 8.2 GB",
    note: "常规版本",
    url: "https://modelscope.cn/models/FlowerCry/gpt-sovits-7z-pacakges/resolve/master/GPT-SoVITS-v2pro-20250604.7z",
  },
  {
    id: "20250531",
    label: "v2Pro 20250531",
    size: "约 8.1 GB",
    note: "较早版本",
    url: "https://modelscope.cn/models/FlowerCry/gpt-sovits-7z-pacakges/resolve/master/GPT-SoVITS-v2pro-20250531.7z",
  },
];
function formatBytes(bytes) {
  if (!bytes) return "";
  const mb = bytes / 1024 / 1024;
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(1)} MB`;
}

function initSettings() {
  document.body.classList.add("status-cards-collapsed");
  bindSettingsEvents();
  prepareSettingsSections();
  loadStatus();
  loadSettings();
  loadEmbeddingStatus();
  loadAsrStatus();
  loadSeparatorStatus();
  loadGptSoVitsStatus();
}

function bindSettingsEvents() {
  bindIf("refresh-status", "click", refreshSystemStatus);
  bindIf("collapse-status", "click", toggleStatusCards);
  bindIf("settings-form", "submit", requestSettingsSave);
  bindIf("reset-settings", "click", requestSettingsReset);
  bindIf("llm-provider", "change", applyLlmPreset);
  ["openai-api-key", "web-search-api-key"].forEach((id) => {
    bindIf(`toggle-${id}`, "click", () => toggleApiKeyVisibility(id));
    bindIf(`copy-${id}`, "click", () => copyApiKey(id));
  });
  ["chunk-size", "chunk-overlap"].forEach((id) => bindIf(id, "input", renderChunkWarning));
  bindIf("web-search-enabled", "change", renderWebSearchSettings);
  bindIf("web-search-provider", "change", renderWebSearchSettings);
  ["web-search-api-key", "web-search-base-url"].forEach((id) => bindIf(id, "input", renderWebSearchSettings));
  bindIf("save-asr", "click", saveAsrConfig);
  bindIf("install-asr", "click", installAsr);
  bindIf("cancel-asr", "click", cancelAsr);
  bindIf("remove-asr", "click", removeAsr);
  bindIf("open-asr-directory", "click", openAsrDirectory);
  bindIf("install-embedding", "click", installEmbedding);
  bindIf("cancel-embedding", "click", cancelEmbedding);
  bindIf("remove-embedding", "click", removeEmbedding);
  bindIf("open-embedding-directory", "click", openEmbeddingDirectory);
  bindIf("tts-enabled", "change", saveTtsConfig);
  bindIf("tts-use-gpu", "change", saveTtsConfig);
  bindIf("install-tts", "click", installTts);
  bindIf("cancel-tts", "click", cancelTts);
  bindIf("remove-tts", "click", removeTts);
  bindIf("open-tts-directory", "click", openTtsDirectory);
  bindIf("install-separator", "click", installSeparator);
  bindIf("cancel-separator", "click", cancelSeparator);
  bindIf("remove-separator", "click", removeSeparator);
  bindIf("open-separator-directory", "click", openSeparatorDirectory);
  bindIf("save-gptsovits-config", "click", saveGptSoVitsConfig);
  bindIf("detect-gptsovits", "click", detectGptSoVits);
  bindIf("gptsovits-preset", "change", applyGptSoVitsPreset);
  bindIf("gptsovits-download-url", "input", syncGptSoVitsPreset);
  bindIf("install-gptsovits", "click", installGptSoVits);
  bindIf("cancel-gptsovits", "click", cancelGptSoVitsInstall);
  bindIf("start-gptsovits-service", "click", startGptSoVitsService);
  bindIf("stop-gptsovits-service", "click", stopGptSoVitsService);
  bindIf("open-gptsovits-directory", "click", openGptSoVitsDirectory);
  bindIf("remove-gptsovits", "click", removeGptSoVitsInstall);
  bindIf("preview-tts", "click", previewTts);
  document.querySelectorAll("[data-collapsible]").forEach((section) => section.addEventListener("toggle", () => {
    const label = section.querySelector(".section-toggle-label");
    if (label) label.textContent = section.open ? "收起" : "展开";
  }));
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
    headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" },
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
  } catch (reason) { setText("settings-status", reason, true); }
}
async function copyApiKey(inputId) {
  try {
    const value = await ensureApiKeyValue(inputId);
    if (!value) return;
    await navigator.clipboard.writeText(value);
    setText("settings-status", "API Key 已复制");
  } catch (reason) { setText("settings-status", `复制失败：${reason.message || reason}`, true); }
}
function resetApiKeyInputs() {
  Object.keys(API_KEY_FIELDS).forEach((id) => {
    $(id).value = "";
    $(id).type = "password";
    setApiKeyVisibilityIcon(id, false);
  });
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
    `Embedding：本地 Qwen3-Embedding-0.6B · 设备：${$("embedding-device").selectedOptions[0].textContent}`,
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
    ttsGuide.querySelector("p").textContent = "Lunar 引擎：下载约 2.2 GB 的 Qwen3-TTS GGUF 模型即可使用，适合快速参考音色克隆；GPT-SoVITS 引擎：需要训练式音色时在下方下载整合包，并在声音页“训练”环节训练专属模型。选择引擎后，另一引擎会自动停用。";
  }
}
async function loadSettings() {
  try {
    const config = await api(fetch("/api/settings"));
    const keyPlaceholder = (configured) => configured ? "已保存，可输入新 Key 替换" : "请输入 API Key";
    $("openai-api-key").placeholder = keyPlaceholder(config.openai_api_key_configured);
    $("web-search-api-key").placeholder = keyPlaceholder(config.web_search_api_key_configured);
    state.openaiKeyConfigured = config.openai_api_key_configured;
    state.webSearchKeyConfigured = config.web_search_api_key_configured;
    $("openai-base-url").value = config.openai_base_url; $("openai-model").value = config.openai_model;
    $("web-search-base-url").value = config.web_search_base_url;
    $("llm-provider").value = inferProvider(LLM_PRESETS, config.openai_base_url);
    $("embedding-device").value = config.embedding_device;
    $("chunk-size").value = config.chunk_size;
    $("chunk-overlap").value = config.chunk_overlap;
    $("web-search-enabled").checked = config.enable_web_fallback;
    $("web-search-provider").value = config.web_search_provider === "off" ? "bocha" : config.web_search_provider;
    renderEmbeddingInstallAction(); renderChunkWarning(); renderWebSearchSettings();
  } catch (reason) { setText("settings-status", reason, true); }
}
async function loadEmbeddingStatus() {
  try {
    const config = await api(fetch("/api/embedding/status"));
    state.embeddingConfigured = config.ready;
    state.embeddingInstalledModel = config.installed ? config.model_id : "";
    state.embeddingResourceStatus = config;
    const embeddingState = config.installing ? "installing" : config.ready ? "ready" : "not_installed";
    const phaseNames = { preparing: "准备安装", runtime: "安装运行环境", model: "下载模型", loading: "加载并探测维度", cancelling: "正在取消", complete: "已就绪", error: "安装失败" };
    $("embedding-state").textContent = config.installing ? (phaseNames[config.phase] || "处理中") : config.ready ? "已就绪" : "尚未安装";
    const device = config.actual_device ? ` · ${config.actual_device.toUpperCase()}` : "";
    setText("embedding-status", config.error || (config.ready ? `${config.model_id} · ${config.dimensions} 维${device} · ${config.model_dir}` : `${config.model_id} · ${config.source === "modelscope" ? "ModelScope" : "Hugging Face"}`));
    const progress = $("embedding-progress");
    if (progress) { progress.classList.toggle("is-hidden", !config.installing); if (config.progress_percent == null) progress.removeAttribute("value"); else progress.value = config.progress_percent; }
    setText("embedding-progress-detail", config.installing ? `${phaseNames[config.phase] || "处理中"}${config.elapsed_seconds ? ` · 已用时 ${config.elapsed_seconds} 秒` : ""}` : "");
    renderEmbeddingInstallAction();
    setHidden("cancel-embedding", !config.installing);
    setDisabled("cancel-embedding", !config.installing || config.cancelling);
    setDisabled("remove-embedding", config.installing || !config.installed);
    setDisabled("open-embedding-directory", config.installing);
    renderServiceStatus("embedding", "Embedding", embeddingState, embeddingState);
    if (config.installing) setTimeout(loadEmbeddingStatus, 2000);
  } catch (reason) {
    state.embeddingConfigured = false;
    setText("embedding-status", `嵌入服务不可用：${friendlyError(reason)}`, true);
  }
}
function embeddingResourcePayload() {
  return { model_id: "Qwen/Qwen3-Embedding-0.6B", source: "modelscope", device: $("embedding-device").value };
}
async function installEmbedding() {
  if (!validateSettings()) return;
  if (!confirm("将自动下载并安装本地 Embedding 模型（Qwen3-Embedding-0.6B），是否继续？")) return;
  setDisabled("install-embedding", true);
  const installButton = $("install-embedding");
  if (installButton) installButton.textContent = "安装中…";
  try {
    await api(fetch("/api/embedding/install", { method: "POST", headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" }, body: JSON.stringify(embeddingResourcePayload()) }));
    await loadEmbeddingStatus();
  } catch (reason) { setText("embedding-status", `安装失败：${friendlyError(reason)}`, true); setDisabled("install-embedding", false); }
}
async function cancelEmbedding() {
  setDisabled("cancel-embedding", true);
  try {
    await api(fetch("/api/embedding/install/cancel", { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    await loadEmbeddingStatus();
  } catch (reason) { setText("embedding-status", `取消失败：${friendlyError(reason)}`, true); setDisabled("cancel-embedding", false); }
}
async function removeEmbedding() {
  if (!confirm("删除当前本地 Embedding 模型？Milvus 中的资料不会被删除。")) return;
  setDisabled("remove-embedding", true);
  try {
    await api(fetch("/api/embedding/model", { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    await loadEmbeddingStatus();
  } catch (reason) { setText("embedding-status", `删除失败：${friendlyError(reason)}`, true); setDisabled("remove-embedding", false); }
}
async function openEmbeddingDirectory() {
  setDisabled("open-embedding-directory", true);
  try {
    const result = await api(fetch("/api/embedding/model-directory", { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
    setText("embedding-status", `已打开：${result.opened_directory}`);
  } catch (reason) { setText("embedding-status", `打开失败：${friendlyError(reason)}`, true); }
  finally { setDisabled("open-embedding-directory", false); }
}
async function loadAsrStatus() {
  try {
    const config = await api(fetch("/api/asr/status"));
    state.asrConfigured = config.ready;
    updateComposerControls();
    if (!$("asr-enabled")) return;
    $("asr-enabled").checked = config.enabled;
    $("asr-python-path").value = config.python_path || "";
    $("asr-model-path").value = config.model_path || "";
    $("asr-ffmpeg-path").value = config.ffmpeg_path || "";
    const asrState = config.installing ? "installing" : config.ready ? "ready" : config.enabled ? "not_installed" : "disabled";
    $("asr-state").textContent = { installing: "正在安装", ready: "已就绪", not_installed: "尚未安装", disabled: "已关闭" }[asrState];
    renderServiceStatus("asr", "ASR", asrState, asrState);
    setText("asr-status", config.error || (config.ready ? `Qwen3-ASR-0.6B · ${config.resolved_model}` : config.download_size));
    const phaseNames = { preparing: "准备安装", runtime: "安装运行环境", model: "从 ModelScope 下载模型", ffmpeg: "准备 FFmpeg", cancelling: "正在取消", complete: "已就绪", error: "安装失败" };
    const progress = $("asr-progress");
    if (progress) { progress.classList.toggle("is-hidden", !config.installing); if (config.progress_percent == null) progress.removeAttribute("value"); else progress.value = config.progress_percent; }
    setText("asr-progress-detail", config.installing ? `${phaseNames[config.phase] || "处理中"}${config.elapsed_seconds ? ` · 已用时 ${config.elapsed_seconds} 秒` : ""}` : "");
    setDisabled("install-asr", config.installing || config.installed);
    const installAsrLabel = $("install-asr");
    if (installAsrLabel) installAsrLabel.textContent = config.installing ? "安装中…" : config.installed ? "已安装" : "安装";
    setHidden("cancel-asr", !config.installing);
    setDisabled("cancel-asr", !config.installing || config.cancelling);
    setDisabled("remove-asr", config.installing || !config.managed_installed);
    updateComposerControls();
    if (config.installing) setTimeout(loadAsrStatus, 2000);
  } catch (reason) {
    state.asrConfigured = false;
    updateComposerControls();
    if (!$("asr-enabled")) return;
    renderServiceStatus("asr", "ASR", "unavailable");
    setText("asr-status", `语音识别服务不可用：${friendlyError(reason)}`, true);
  }
}
async function saveAsrConfig() {
  setDisabled("save-asr", true);
  try {
    await api(fetch("/api/asr/config", { method: "PATCH", headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" }, body: JSON.stringify({ enabled: $("asr-enabled").checked, python_path: $("asr-python-path").value.trim(), model_path: $("asr-model-path").value.trim(), ffmpeg_path: $("asr-ffmpeg-path").value.trim() }) }));
    await loadAsrStatus();
  } catch (reason) { setText("asr-status", `保存失败：${friendlyError(reason)}`, true); }
  finally { setDisabled("save-asr", false); }
}
async function installAsr() {
  if (!confirm("将下载约 5-10 GB 的 CUDA 运行环境和 Qwen3-ASR 模型，是否继续？")) return;
  setDisabled("install-asr", true);
  const installButton = $("install-asr");
  if (installButton) installButton.textContent = "安装中…";
  try {
    await saveAsrConfig();
    await api(fetch("/api/asr/install", { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
    await loadAsrStatus();
  } catch (reason) { setText("asr-status", `安装失败：${friendlyError(reason)}`, true); setDisabled("install-asr", false); }
}
async function removeAsr() {
  if (!confirm("删除项目自动下载的 ASR 环境和模型？外部目录不会被删除。")) return;
  setDisabled("remove-asr", true);
  try {
    await api(fetch("/api/asr/install", { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    await loadAsrStatus();
  } catch (reason) { setText("asr-status", `删除失败：${friendlyError(reason)}`, true); setDisabled("remove-asr", false); }
}
async function cancelAsr() {
  setDisabled("cancel-asr", true);
  try {
    await api(fetch("/api/asr/install/cancel", { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    await loadAsrStatus();
  } catch (reason) { setText("asr-status", `取消失败：${friendlyError(reason)}`, true); setDisabled("cancel-asr", false); }
}
async function openAsrDirectory() {
  const button = $("open-asr-directory");
  if (!button) return;
  button.disabled = true;
  try {
    const result = await api(fetch("/api/asr/model-directory", { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
    setText("asr-status", `已打开：${result.opened_directory}`);
  } catch (reason) { setText("asr-status", `打开失败：${friendlyError(reason)}`, true); }
  finally { button.disabled = false; }
}
async function loadTtsStatus() {
  try {
    const config = await api(fetch("/api/tts/status"));
    state.ttsConfigured = config.ready;
    updateComposerControls();
    if (!$("tts-enabled")) return;
    $("tts-enabled").checked = config.enabled;
    $("tts-use-gpu").checked = config.use_gpu;
    setDisabled("tts-use-gpu", config.installing);
    document.querySelectorAll('input[name="tts-engine"]').forEach((input) => {
      input.checked = input.value === config.engine;
      input.disabled = config.installing;
    });
    const gptEngine = config.engine === "gpt_sovits";
    setDisabled("tts-enabled", gptEngine || config.installing);
    const lunarState = $("tts-engine-lunar-state");
    if (lunarState) {
      lunarState.textContent = gptEngine ? "已停用" : config.installed ? "已就绪" : "未安装";
      lunarState.classList.toggle("is-error", gptEngine);
    }
    setDisabled("install-tts", gptEngine || config.installing || config.variant_installed || !config.runtime_bundled);
    setDisabled("remove-tts", gptEngine || config.installing || !(config.variants || []).some((v) => v.installed));
    setDisabled("open-tts-directory", gptEngine || config.installing);
    setDisabled("preview-tts", gptEngine || !config.ready);
    if (gptEngine && $("install-tts")) $("install-tts").textContent = "已停用（当前使用 GPT-SoVITS）";
    else if (config.installing) $("install-tts").textContent = "安装中";
    else if (config.variant_installed) $("install-tts").textContent = "已安装";
    else $("install-tts").textContent = "自动下载安装";
    setText("tts-engine-hint", gptEngine
      ? "已启用 GPT-SoVITS 训练引擎，Lunar 引擎已停用；未安装时请在下方下载整合包"
      : "Lunar 引擎用于快速参考音色克隆；GPT-SoVITS 用于训练式专属音色");
    if (gptEngine && config.ready) {
      setText("tts-status", "当前使用 GPT-SoVITS 引擎（Lunar 已停用）");
    }
    const ttsState = config.installing ? "installing" : config.ready ? "ready" : config.enabled ? "not_installed" : "disabled";
    renderServiceStatus("tts", "TTS", ttsState, ttsState);
    const size = (bytes) => bytes ? `${(bytes / 1024 / 1024).toFixed(bytes > 1024 * 1024 * 100 ? 0 : 1)} MB` : "";
    const duration = (seconds) => seconds == null ? "正在估算剩余时间" : seconds < 60 ? `预计剩余 ${seconds} 秒` : `预计剩余 ${Math.ceil(seconds / 60)} 分钟`;
    const phaseNames = { preparing: "准备下载", model: "下载模型", cancelling: "正在取消", complete: "安装完成", error: "安装失败" };
    const ttsStateNode = $("tts-state");
    if (ttsStateNode) ttsStateNode.textContent = config.installing ? (phaseNames[config.phase] || "正在安装") : config.ready ? "已就绪" : config.enabled ? "尚未安装" : "已关闭";
    setText("tts-status", config.error || (!config.runtime_bundled ? "当前开发目录缺少内置 Lunar TTS 运行库；完整 Windows 发布包将自带该文件" : config.ready ? `Qwen3-TTS-0.6B · ${config.model_dir}` : config.download_size));
    const progress = $("tts-progress");
    if (progress) { progress.classList.toggle("is-hidden", !config.installing); if (config.progress_percent == null) progress.removeAttribute("value"); else progress.value = config.progress_percent; }
    const speed = config.download_speed_bytes ? `${size(config.download_speed_bytes)}/s` : "";
    const percent = config.progress_percent != null ? `${config.progress_percent}%` : "";
    const elapsed = config.elapsed_seconds ? `已用时 ${config.elapsed_seconds} 秒` : "";
    setText("tts-progress-detail", config.installing ? [percent, config.source ? `源：${config.source}` : "", config.current_file, size(config.downloaded_bytes), config.total_bytes ? `/ ${size(config.total_bytes)}` : "", speed, duration(config.eta_seconds), elapsed].filter(Boolean).join(" · ") : "");
    setHidden("cancel-tts", !config.installing);
    setDisabled("cancel-tts", !config.installing || config.cancelling);
    state.ttsStatus = config;
    if (state.editPersona) {
      const reference = await api(fetch(`/api/tts/personas/${state.editPersona.id}/reference`, { headers: { "X-YUMENO-Request": "web" } }));
      syncEditTtsPreview(reference.configured);
    }
    if (config.installing) setTimeout(loadTtsStatus, 1500);
  } catch (reason) {
    state.ttsConfigured = false;
    updateComposerControls();
    if (!$("tts-enabled")) return;
    renderServiceStatus("tts", "TTS", "unavailable");
    setText("tts-status", `语音服务不可用：${friendlyError(reason)}`, true);
  }
}
async function saveTtsConfig() {
  try {
    const engineInput = document.querySelector('input[name="tts-engine"]:checked');
    await api(fetch("/api/tts/config", { method: "PATCH", headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" }, body: JSON.stringify({ enabled: $("tts-enabled").checked, use_gpu: $("tts-use-gpu").checked, engine: engineInput ? engineInput.value : undefined }) }));
    await loadTtsStatus();
  } catch (reason) { setText("tts-status", `保存失败：${friendlyError(reason)}`, true); }
}
async function installTts() {
  if (!confirm("将下载约 2.2 GB 的 Qwen3-TTS GGUF 模型（F16 标准版），Lunar TTS 运行库已随应用内置。是否继续？")) return;
  setDisabled("install-tts", true);
  try {
    await api(fetch("/api/tts/install", { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
    await loadTtsStatus();
  } catch (reason) { setText("tts-status", `安装失败：${friendlyError(reason)}`, true); setDisabled("install-tts", false); }
}
async function removeTts() {
  if (!confirm("删除已下载的 TTS 模型？内置运行库和角色参考声音不会删除。")) return;
  setDisabled("remove-tts", true);
  try {
    await api(fetch("/api/tts/install", { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    await loadTtsStatus();
  } catch (reason) { setText("tts-status", `删除失败：${friendlyError(reason)}`, true); setDisabled("remove-tts", false); }
}
async function cancelTts() {
  setDisabled("cancel-tts", true);
  try {
    await api(fetch("/api/tts/install/cancel", { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    await loadTtsStatus();
  } catch (reason) { setText("tts-status", `取消失败：${friendlyError(reason)}`, true); setDisabled("cancel-tts", false); }
}
async function openTtsDirectory() {
  setDisabled("open-tts-directory", true);
  try {
    const result = await api(fetch("/api/tts/model-directory", { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
    setText("tts-status", `已打开：${result.opened_directory}`);
  } catch (reason) { setText("tts-status", `打开失败：${friendlyError(reason)}`, true); }
  finally { setDisabled("open-tts-directory", false); }
}
async function loadSeparatorStatus() {
  try {
    const config = await api(fetch("/api/tts/separator/status", { headers: { "X-YUMENO-Request": "web" } }));
    if (!$("separator-state")) return;
    const phaseNames = { preparing: "准备下载", model: "下载人声分离模型", cancelling: "正在取消", complete: "安装完成", error: "安装失败" };
    $("separator-state").textContent = config.installing ? (phaseNames[config.phase] || "正在安装") : config.ready ? "已就绪" : "尚未安装";
    setText("separator-status", config.error || (config.ready ? "HT-Demucs FT · 纯 ONNX 推理，无需 PyTorch" : "从视频提取角色音色需要此模型（约 165 MB）"));
    const progress = $("separator-progress");
    if (progress) { progress.classList.toggle("is-hidden", !config.installing); if (config.progress_percent == null) progress.removeAttribute("value"); else progress.value = config.progress_percent; }
    const size = (bytes) => bytes ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : "";
    setText("separator-progress-detail", config.installing ? `${size(config.downloaded_bytes)} / ${size(config.total_bytes)}${config.elapsed_seconds ? ` · 已用时 ${config.elapsed_seconds} 秒` : ""}` : "");
    setDisabled("install-separator", config.installing || config.installed);
    const installLabel = $("install-separator");
    if (installLabel) installLabel.textContent = config.installing ? "安装中…" : config.installed ? "已安装" : "安装";
    setHidden("cancel-separator", !config.installing);
    setDisabled("cancel-separator", !config.installing || config.cancelling);
    setDisabled("remove-separator", config.installing || !config.installed);
    setDisabled("open-separator-directory", config.installing);
    if (config.installing) setTimeout(loadSeparatorStatus, 1500);
  } catch (reason) {
    if (!$("separator-state")) return;
    setText("separator-status", `人声分离服务不可用：${friendlyError(reason)}`, true);
  }
}
async function installSeparator() {
  if (!confirm("将下载约 165 MB 的 HT-Demucs 人声分离模型（纯 ONNX，不引入 PyTorch）。是否继续？")) return;
  setDisabled("install-separator", true);
  try {
    await api(fetch("/api/tts/separator/install", { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
    await loadSeparatorStatus();
  } catch (reason) { setText("separator-status", `安装失败：${friendlyError(reason)}`, true); setDisabled("install-separator", false); }
}
async function cancelSeparator() {
  setDisabled("cancel-separator", true);
  try {
    await api(fetch("/api/tts/separator/install/cancel", { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    await loadSeparatorStatus();
  } catch (reason) { setText("separator-status", `取消失败：${friendlyError(reason)}`, true); setDisabled("cancel-separator", false); }
}
async function removeSeparator() {
  if (!confirm("删除已下载的人声分离模型？下次从视频提取音色前需重新安装。")) return;
  setDisabled("remove-separator", true);
  try {
    await api(fetch("/api/tts/separator/install", { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    await loadSeparatorStatus();
  } catch (reason) { setText("separator-status", `删除失败：${friendlyError(reason)}`, true); setDisabled("remove-separator", false); }
}
  async function openSeparatorDirectory() {
    setDisabled("open-separator-directory", true);
    try {
      const result = await api(fetch("/api/tts/separator/model-directory", { headers: { "X-YUMENO-Request": "web" } }));
      setText("separator-status", `已打开：${result.opened_directory}`);
    } catch (reason) { setText("separator-status", `打开失败：${friendlyError(reason)}`, true); }
    finally { setDisabled("open-separator-directory", false); }
  }
  async function loadGptSoVitsStatus() {
    try {
      const status = await api(fetch("/api/gpt-sovits/status"));
      if (!$("gptsovits-state")) return;
      const install = status.install || {};
      const size = formatBytes;
      const phaseNames = {
        preparing: "准备下载",
        download: "下载整合包",
        extracting: "解压整合包",
        patching: "应用项目补丁",
        cleaning: "清理冗余文件",
        verifying: "校验安装",
        complete: "安装完成",
        cancelling: "正在取消",
        error: "安装失败",
        idle: "",
      };
      const phaseLabel = install.installing ? (phaseNames[install.phase] || "处理中") : "";
      $("gptsovits-state").textContent = install.installing
        ? phaseLabel
        : status.ready ? "已就绪"
        : status.installed ? "已安装"
        : status.configured ? "未安装" : "未配置";
      setText("gptsovits-status", install.error || (
        install.installing
          ? `${phaseLabel}${install.detail ? ` · ${install.detail}` : ""}`
          : status.ready
          ? `GPT-SoVITS API 服务运行中 · ${status.api_script}`
          : status.installed
            ? `已安装 · ${status.install_dir}`
            : status.configured
              ? "已配置路径，但未找到可用安装"
              : "未配置 GPT-SoVITS 安装（可自动检测或下载整合包）"
      ));
      const progress = $("gptsovits-progress");
      if (progress) {
        progress.classList.toggle("is-hidden", !install.installing);
        if (install.progress_percent == null) progress.removeAttribute("value");
        else progress.value = install.progress_percent;
      }
      const detailParts = [];
      if (install.installing) {
        if (install.progress_percent != null) detailParts.push(`${install.progress_percent}%`);
        if (install.current_file) detailParts.push(install.current_file);
        if (["download", "extracting"].includes(install.phase)) {
          if (install.downloaded_bytes) detailParts.push(size(install.downloaded_bytes));
          if (install.total_bytes) detailParts.push(`/ ${size(install.total_bytes)}`);
          if (install.download_speed_bytes) detailParts.push(`${size(install.download_speed_bytes)}/s`);
          if (install.eta_seconds != null) detailParts.push(`剩余 ${install.eta_seconds} 秒`);
        }
        if (install.detail) detailParts.push(install.detail);
        if (install.elapsed_seconds) detailParts.push(`已用 ${install.elapsed_seconds} 秒`);
      }
      setText("gptsovits-progress-detail", detailParts.join(" · "));
      $("gptsovits-install-dir").value = status.install_dir || "";
      const urlInput = $("gptsovits-download-url");
      if (urlInput) {
        const persistedUrl = (install.download_url || "").trim();
        if (!urlInput.value.trim()) urlInput.value = persistedUrl;
        if (!urlInput.value.trim()) applyGptSoVitsPreset();
        else syncGptSoVitsPreset();
      }
      const gptState = $("tts-engine-gpt-state");
      if (gptState) {
        gptState.textContent = status.ready ? "运行中" : status.installed ? "已就绪" : "未安装";
        gptState.classList.toggle("is-error", !status.installed && status.configured);
      }
      const installLabel = $("install-gptsovits");
      if (installLabel) {
        installLabel.textContent = install.installing
          ? "安装中…"
          : status.installed ? "已安装" : "安装";
      }
      setDisabled("install-gptsovits", install.installing || status.ready);
      setHidden("cancel-gptsovits", !install.installing);
      setDisabled("detect-gptsovits", install.installing);
      setDisabled("open-gptsovits-directory", install.installing || !status.installed);
      setDisabled("remove-gptsovits", install.installing || !status.installed);
      setDisabled("start-gptsovits-service", install.installing || status.ready || !status.installed);
      setDisabled("stop-gptsovits-service", !status.service_running);
      setText("gptsovits-service-info", status.service_running
        ? `GPT-SoVITS API 服务运行中 · 端口 ${status.api_port}`
        : status.installed ? "GPT-SoVITS 服务未运行（合成时自动启动）" : "");
      if (install.installing) setTimeout(loadGptSoVitsStatus, 1500);
    } catch (reason) {
      setText("gptsovits-status", `不可用：${friendlyError(reason)}`, true);
    }
  }
  async function saveGptSoVitsConfig() {
    try {
      const dir = $("gptsovits-install-dir").value.trim();
      const url = $("gptsovits-download-url").value.trim();
      await api(fetch("/api/gpt-sovits/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" },
        body: JSON.stringify({ install_dir: dir || null, download_url: url || null }),
      }));
      await loadGptSoVitsStatus();
      setText("gptsovits-config-status", "已保存");
    } catch (reason) {
      setText("gptsovits-config-status", `保存失败：${friendlyError(reason)}`, true);
    }
  }
  function applyGptSoVitsPreset() {
    const preset = GPT_SOVITS_PRESETS.find((item) => item.id === $("gptsovits-preset").value);
    const urlInput = $("gptsovits-download-url");
    const note = $("gptsovits-preset-note");
    if (!urlInput) return;
    if (!preset) {
      if (note) note.textContent = "";
      return;
    }
    urlInput.value = preset.url;
    if (note) note.textContent = `${preset.label} · ${preset.size} · ${preset.note}`;
  }
  function syncGptSoVitsPreset() {
    const select = $("gptsovits-preset");
    const urlInput = $("gptsovits-download-url");
    const note = $("gptsovits-preset-note");
    if (!select || !urlInput) return;
    const url = urlInput.value.trim();
    const preset = GPT_SOVITS_PRESETS.find((item) => item.url === url);
    select.value = preset ? preset.id : "custom";
    if (note) note.textContent = preset ? `${preset.label} · ${preset.size} · ${preset.note}` : "";
  }
  async function detectGptSoVits() {
    setDisabled("detect-gptsovits", true);
    try {
      const status = await api(fetch("/api/gpt-sovits/detect", { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
      if (status.install_dir) setText("gptsovits-status", `已检测到：${status.install_dir}`);
      else setText("gptsovits-status", "未检测到可用安装", true);
      await loadGptSoVitsStatus();
    } catch (reason) {
      setText("gptsovits-status", `检测失败：${friendlyError(reason)}`, true);
      setDisabled("detect-gptsovits", false);
    }
  }
  async function installGptSoVits() {
    const url = $("gptsovits-download-url").value.trim();
    if (!url) return setText("gptsovits-status", "请先填写整合包下载地址（zip）", true);
    setDisabled("install-gptsovits", true);
    try {
      await api(fetch("/api/gpt-sovits/install", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" },
        body: JSON.stringify({ url }),
      }));
      await loadGptSoVitsStatus();
    } catch (reason) {
      setText("gptsovits-status", `安装失败：${friendlyError(reason)}`, true);
      setDisabled("install-gptsovits", false);
    }
  }
  async function cancelGptSoVitsInstall() {
    try {
      await api(fetch("/api/gpt-sovits/install/cancel", { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
      await loadGptSoVitsStatus();
    } catch (reason) {
      setText("gptsovits-status", `取消失败：${friendlyError(reason)}`, true);
    }
  }
  async function startGptSoVitsService() {
    setDisabled("start-gptsovits-service", true);
    try {
      await api(fetch("/api/gpt-sovits/service/start", { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
      await loadGptSoVitsStatus();
    } catch (reason) {
      setText("gptsovits-status", `启动失败：${friendlyError(reason)}`, true);
      setDisabled("start-gptsovits-service", false);
    }
  }
  async function stopGptSoVitsService() {
    setDisabled("stop-gptsovits-service", true);
    try {
      await api(fetch("/api/gpt-sovits/service/stop", { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
      await loadGptSoVitsStatus();
    } catch (reason) {
      setText("gptsovits-status", `停止失败：${friendlyError(reason)}`, true);
      setDisabled("stop-gptsovits-service", false);
    }
  }
  async function openGptSoVitsDirectory() {
    setDisabled("open-gptsovits-directory", true);
    try {
      const result = await api(fetch("/api/gpt-sovits/model-directory", { method: "POST", headers: { "X-YUMENO-Request": "web" } }));
      setText("gptsovits-status", `已打开：${result.opened_directory}`);
    } catch (reason) {
      setText("gptsovits-status", `打开失败：${friendlyError(reason)}`, true);
    } finally {
      setDisabled("open-gptsovits-directory", false);
    }
  }
  async function removeGptSoVitsInstall() {
    if (!confirm("删除项目内的 GPT-SoVITS 引擎（约 12GB）？删除后需要重新下载或复制整合包才能恢复。")) return;
    setDisabled("remove-gptsovits", true);
    try {
      await api(fetch("/api/gpt-sovits/install", { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
      await loadGptSoVitsStatus();
    } catch (reason) {
      setText("gptsovits-status", `删除失败：${friendlyError(reason)}`, true);
      setDisabled("remove-gptsovits", false);
    }
  }
  async function loadGptSoVitsAssets() {
    try {
      const data = await api(fetch("/api/voice-assets"));
      const list = $("gptsovits-assets");
      if (!list) return;
      list.replaceChildren();
      const assets = (data.items || []).filter((item) => item.status === "ready");
      if (!assets.length) {
        const empty = document.createElement("li");
        empty.className = "gptsovits-asset-empty";
        empty.textContent = "暂无训练音色，请到声音工坊的“训练”环节生成";
        list.append(empty);
        return;
      }
      assets.forEach((item) => {
        const li = document.createElement("li");
        li.className = "gptsovits-asset";
        const info = document.createElement("span");
        info.innerHTML = "<b></b><em></em>";
        info.querySelector("b").textContent = item.name;
        info.querySelector("em").textContent = "GPT-SoVITS · 可绑定角色";
        const actions = document.createElement("span");
        actions.className = "asr-actions";
        const preview = document.createElement("button");
        preview.className = "button button-secondary";
        preview.type = "button";
        preview.textContent = "试听";
        preview.onclick = () => previewVoiceAsset(item.id);
        actions.append(preview);
        const del = document.createElement("button");
        del.className = "button button-danger";
        del.type = "button";
        del.textContent = "删除";
        del.onclick = () => deleteVoiceAsset(item.id);
        actions.append(del);
        li.append(info, actions);
        list.append(li);
      });
    } catch (reason) {
      setText("gptsovits-status", `音色加载失败：${friendlyError(reason)}`, true);
    }
  }
  async function previewVoiceAsset(assetId) {
    try {
      const response = await fetch(`/api/voice-assets/${assetId}/synthesize`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" },
        body: JSON.stringify({ text: "你好，这是我的声音。很高兴认识你。" }),
      });
      if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || "试听失败");
      if (window.PL && window.PL.unlockAudio) window.PL.unlockAudio();
      const audio = new Audio(URL.createObjectURL(await response.blob()));
      audio.play().catch(() => {});
    } catch (reason) {
      setText("gptsovits-status", `试听失败：${friendlyError(reason)}`, true);
    }
  }
  async function deleteVoiceAsset(assetId) {
    if (!confirm("删除该训练音色？项目内的模型文件将一并移除。")) return;
    try {
      await api(fetch(`/api/voice-assets/${assetId}`, { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
      await loadGptSoVitsAssets();
    } catch (reason) {
      setText("gptsovits-status", `删除失败：${friendlyError(reason)}`, true);
    }
  }
  async function previewTts() {
  const text = $("tts-preview-text").value.trim();
  if (!text) return setText("tts-preview-status", "请输入试听文本");
  const button = $("preview-tts");
  if (!button) return;
  button.disabled = true;
  setText("tts-preview-status", "正在生成试听");
  try {
    const response = await fetch("/api/tts/preview", { method: "POST", headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" }, body: JSON.stringify({ text }) });
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
  } catch (reason) { setText("tts-preview-status", `生成失败：${friendlyError(reason)}`, true); }
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
function renderEmbeddingInstallAction() {
  const button = $("install-embedding");
  const status = state.embeddingResourceStatus;
  if (!button) return;
  if (status?.installing) {
    button.textContent = "安装中…";
    button.disabled = true;
  } else if (status?.installed) {
    button.textContent = "已安装";
    button.disabled = true;
  } else {
    button.textContent = "安装";
    button.disabled = false;
  }
}
function renderChunkWarning() {
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
    : "将清除本机前端保存的 LLM、Embedding、联网搜索配置和 Key。不会影响 .env 中的 Milvus 或端口配置。";
  $("settings-confirm-submit").textContent = isSave ? "确认保存" : "确认重置";
  $("settings-confirm-dialog").showModal();
}
function isHttpUrl(value) {
  try { return ["http:", "https:"].includes(new URL(value).protocol); }
  catch { return false; }
}
function validateSettings() {
  const chunkSize = Number($("chunk-size").value);
  const chunkOverlap = Number($("chunk-overlap").value);
  const chunkInvalid = chunkSize < 200 || chunkSize > 4000 || chunkOverlap < 0 || chunkOverlap > 1000 || chunkOverlap > Math.floor(chunkSize / 4);
  const webEnabled = $("web-search-enabled").checked;
  const webInvalid = webEnabled && ((!state.webSearchKeyConfigured && !$("web-search-api-key").value.trim()) || ($("web-search-provider").value === "custom" && !isHttpUrl($("web-search-base-url").value)));
  renderChunkWarning(); renderWebSearchSettings();
  if (chunkInvalid) setText("settings-status", "请检查文档切分参数：重叠长度不能超过切分长度的 25%。");
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
    await api(fetch("/api/settings", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ openai_api_key: value("openai-api-key"), openai_base_url: value("openai-base-url"), openai_model: value("openai-model"), embedding_provider: "managed_local", embedding_model: "Qwen/Qwen3-Embedding-0.6B", embedding_model_source: "modelscope", embedding_device: $("embedding-device").value, chunk_size: Number(value("chunk-size")), chunk_overlap: Number(value("chunk-overlap")), web_search_provider: webEnabled ? $("web-search-provider").value : "off", web_search_api_key: value("web-search-api-key"), web_search_base_url: value("web-search-base-url"), enable_web_fallback: webEnabled }) }));
    resetApiKeyInputs();
    setText("settings-status", "已保存，可立即使用"); await loadSettings();
  } catch (reason) { setText("settings-status", reason, true); }
  finally { $("save-settings").disabled = false; }
}
async function resetSettings() {
  $("reset-settings").disabled = true; setText("settings-status");
  try {
    await api(fetch("/api/settings", { method: "DELETE" }));
    resetApiKeyInputs(); $("web-search-base-url").value = "";
    setText("settings-status", "配置已重置"); await loadSettings();
  } catch (reason) { setText("settings-status", reason, true); }
  finally { $("reset-settings").disabled = false; }
}


