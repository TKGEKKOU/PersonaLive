"use strict";
window.PL = window.PL || { modules: {} };

function ensureExitDialog() {
  const existing = $("exit-confirm-dialog");
  if (existing) return existing;
  const dialog = document.createElement("dialog");
  dialog.id = "exit-confirm-dialog";
  dialog.className = "settings-confirm-dialog";
  dialog.setAttribute("aria-labelledby", "exit-confirm-title");
  dialog.innerHTML = [
    '<form method="dialog">',
    '<h2 id="exit-confirm-title">退出 YUMENO？</h2>',
    '<div class="exit-confirm-detail">',
    '<label class="exit-option"><input type="radio" name="exit-policy" value="pause" checked><span><b>停止服务（推荐）</b><em>暂停 Docker，下次启动自动恢复</em></span></label>',
    '<label class="exit-option"><input type="radio" name="exit-policy" value="keep"><span><b>保持服务</b><em>Docker 保持运行，下次启动最快</em></span></label>',
    '<label class="exit-option"><input type="radio" name="exit-policy" value="remove"><span><b>删除服务</b><em>删除 Docker 容器，数据保留</em></span></label>',
    '</div>',
    '<div class="settings-confirm-actions">',
    '<button id="exit-confirm-cancel" class="button button-secondary" type="button">取消</button>',
    '<button id="exit-confirm-submit" class="button button-danger" type="button"><span class="btn-spinner"></span><span id="exit-confirm-label">安全退出</span></button>',
    '</div>',
    '</form>',
  ].join("");
  document.body.append(dialog);
  $("exit-confirm-cancel").addEventListener("click", () => dialog.close());
  $("exit-confirm-submit").addEventListener("click", async () => {
    const button = $("exit-confirm-submit");
    button.classList.add("is-loading");
    button.disabled = true;
    const label = $("exit-confirm-label");
    if (label) label.textContent = "正在退出…";
    const selected = document.querySelector('input[name="exit-policy"]:checked')?.value || "pause";
    try {
      await fetch("/api/system/docker-settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ on_exit: selected }),
      });
    } catch (e) {}
    if (window.pywebview?.api?.do_exit) {
      window.pywebview.api.do_exit();
    } else {
      if (selected === "remove") {
        try { await fetch("/api/system/docker/remove", { method: "POST" }); } catch (e) {}
      }
      await fetch("/api/system/shutdown", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stop_docker: selected === "pause" }),
      });
    }
  });
  return dialog;
}

window.showExitConfirm = function showExitConfirm() {
  const dialog = ensureExitDialog();
  fetch("/api/system/docker-settings")
    .then((response) => response.json())
    .then((settings) => {
      const radio = document.querySelector(`input[name="exit-policy"][value="${settings.on_exit}"]`);
      if (radio) radio.checked = true;
    })
    .catch(() => {});
  dialog.showModal();
};

const state = {
  draft: null,
  personas: [],
  activePersona: null,
  editPersona: null,
  manageSelectedId: null,
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
  voiceStream: null,
  voiceActive: false,
  pendingVoiceQuestion: "",
  voiceFlushTimer: null,
  voicePlayingAudio: null,
  voicePlaybackEpoch: 0,
  textPaceBuffer: "",
  paceCharsPerTick: 1,
  paceNode: null,
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

const API_KEY_FIELDS = {
  "openai-api-key": { field: "openai_api_key", configured: "openaiKeyConfigured" },
  "embedding-api-key": { field: "embedding_api_key", configured: "embeddingKeyConfigured" },
  "web-search-api-key": { field: "web_search_api_key", configured: "webSearchKeyConfigured" },
};

function icons() { if (window.lucide) window.lucide.createIcons(); }
async function api(request) {
  const response = await request;
  const data = response.headers.get("content-type")?.includes("application/json") ? await response.json() : null;
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `请求失败 (${response.status})`);
  return data;
}
function setText(id, value = "", isError = false) {
  const node = $(id);
  if (!node) return;
  node.textContent = value?.message || value;
  node.classList.toggle("is-error", Boolean(isError));
}

// pywebview 内 target=_blank 不会可靠打开外部链接，统一交给桌面端在系统浏览器打开。
document.addEventListener("click", (event) => {
  const anchor = event.target.closest?.("a[target='_blank']");
  if (!anchor || !window.pywebview?.api?.open_external) return;
  event.preventDefault();
  window.pywebview.api.open_external(anchor.href);
});

async function loadStatus() {
  try {
    const status = await api(fetch("/api/status"));
    renderServiceStatus("sqlite", "SQLite", status.sqlite);
    renderServiceStatus("milvus", "Milvus", status.milvus);
    renderSystemStatusDetail(status);
    const milvusLink = $("settings-open-milvus");
    const sqliteLink = $("settings-open-sqlite");
    const attuPort = status.ports?.attu;
    if (milvusLink && attuPort) {
      milvusLink.href = `http://127.0.0.1:${attuPort}`;
    }
    if (sqliteLink && status.port) {
      sqliteLink.href = `http://127.0.0.1:${status.port}/sqlite/`;
    }
  } catch {
    renderServiceStatus("sqlite", "SQLite", "unavailable");
    renderServiceStatus("milvus", "Milvus", "unavailable");
    const detail = $("system-status-detail");
    if (detail) detail.textContent = "无法获取详细状态，请稍后重试。";
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
  setDetail("sqlite", status.sqlite === "ok"
    ? ["本地 SQLite 数据库已连接"]
    : [status.sqlite === "unavailable" ? "连接失败" : "未初始化"]);
  setDetail("milvus", [
    status.milvus === "ok"
      ? "本地 Milvus 向量数据库已连接"
      : status.milvus === "collection_missing"
        ? "缺少集合，请重建"
        : status.milvus === "unavailable" ? "服务不可用" : "检查中",
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
function details(label, data) { const node = document.createElement("details"); const summary = document.createElement("summary"); summary.textContent = `${label} (${data.length})`; const pre = document.createElement("pre"); pre.textContent = JSON.stringify(data, null, 2); node.append(summary, pre); return node; }
function empty(text) { const node = document.createElement("p"); node.className = "empty-state"; node.textContent = text; return node; }
function openPreview(item) { $("preview-title").textContent = item.original_filename; $("preview-content").textContent = item.markdown_preview || item.error_message || "暂无内容"; $("preview-drawer").classList.add("is-open"); $("preview-backdrop").classList.add("is-open"); }
function closePreview() { $("preview-drawer").classList.remove("is-open"); $("preview-backdrop").classList.remove("is-open"); }
