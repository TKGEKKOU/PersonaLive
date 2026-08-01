"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.settings = { init: initSettings };

function initSettings() {
  bindSettingsEvents();
  prepareSettingsSections();
  loadStatus();
  loadSettings();
  loadEmbeddingStatus();
  loadAsrStatus();
  loadTtsStatus();
}

function bindSettingsEvents() {
  $("refresh-status").addEventListener("click", refreshSystemStatus);
  $("collapse-status").addEventListener("click", toggleStatusCards);
  $("settings-form").addEventListener("submit", requestSettingsSave);
  $("reset-settings").addEventListener("click", requestSettingsReset);
  $("settings-confirm-cancel").addEventListener("click", () => $("settings-confirm-dialog").close());
  $("settings-confirm-submit").addEventListener("click", confirmSettingsAction);
  $("llm-provider").addEventListener("change", applyLlmPreset);
  ["openai-api-key", "embedding-api-key", "web-search-api-key"].forEach((id) => {
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
