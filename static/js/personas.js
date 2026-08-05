"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.create = { init: initCreatePage };
window.PL.modules.manage = { init: initManagePage };
window.PL.modules.test = { init: initTestPage };

const DRAFT_STATUS_LABELS = { analyzing: "分析中", draft: "待确认", confirmed: "已创建" };
const DOCUMENT_STATUS_LABELS = {
  converting: { label: "正在转换为 Markdown", tone: "pending" },
  conversion_failed: { label: "转换失败", tone: "failed" },
  preview_ready: { label: "待写入 Milvus", tone: "pending" },
  indexing: { label: "正在写入 Milvus 向量库", tone: "pending" },
  indexed: { label: "已写入 Milvus 向量库", tone: "ok" },
  index_failed: { label: "Milvus 写入失败", tone: "failed" },
};
const CREATE_STEP_ORDER = ["upload", "analyze", "confirm"];

function initCreatePage() {
  bindCreateEvents();
  setupCreateDropZone();
  bindPreviewClose();
}

function initManagePage() {
  bindManageEvents();
  bindPreviewClose();
  loadPersonas();
}

function initTestPage() {
  loadEvalPersonas();
  bindEvalEvents();
}

function bindSafe(id, event, handler) {
  const node = $(id);
  if (node) node.addEventListener(event, handler);
}

function bindCreateEvents() {
  bindSafe("document-files", "change", () => summarizeFiles("document-files", "file-summary", "未选择文件"));
  bindSafe("batch-form", "submit", uploadDraft);
  bindSafe("reset-batch", "click", resetDraft);
  bindSafe("save-draft", "click", saveDraft);
  bindSafe("confirm-draft", "click", confirmDraft);
}

function bindManageEvents() {
  bindSafe("save-all-persona", "click", requestSaveAll);
  bindSafe("save-all-cancel", "click", () => $("save-all-dialog").close());
  bindSafe("save-all-confirm", "click", confirmSaveAll);
  bindSafe("edit-files-confirm", "click", () => saveEditFiles());
  bindSafe("edit-live2d-confirm", "click", () => saveEditLive2d());
  bindSafe("edit-tts-confirm", "click", () => saveEditVoice());
  bindSafe("edit-document-files", "change", () => addSelectedFiles("edit-document-files", "edit-files-selected", "files"));
  bindSafe("edit-tts-reference", "change", () => addSelectedFiles("edit-tts-reference", "edit-tts-selected", "audio"));
  setupDropZone("edit-files-drop", "edit-document-files", "edit-files-selected", "files");
  setupDropZone("edit-tts-drop", "edit-tts-reference", "edit-tts-selected", "audio");
  bindSafe("edit-tts-preview-reference", "click", playEditReference);
  bindSafe("edit-tts-generate-preview", "click", generateEditPreview);
  bindSafe("edit-tts-open-settings", "click", openTtsSettings);
  bindSafe("edit-tts-remove-reference", "click", removeEditReference);
  bindSafe("edit-tts-enabled", "change", syncEditTtsControls);
  bindSafe("edit-mcp-grants-save", "click", saveEditMCPGrants);
  bindSafe("delete-persona", "click", requestPersonaDeletion);
  bindSafe("delete-persona-cancel", "click", () => $("delete-persona-dialog").close());
  bindSafe("delete-persona-confirm", "click", confirmPersonaDeletion);
}

function bindPreviewClose() {
  bindSafe("close-preview", "click", closePreview);
  bindSafe("preview-backdrop", "click", closePreview);
}
function moduleMessage(id, text, isError = false) {
  const node = $(id);
  if (!node) return;
  node.textContent = text || "";
  node.classList.toggle("is-error", isError);
}
function formatFileSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
function selectedFilesKey(kind) { return kind === "audio" ? "editSelectedAudio" : "editSelectedFiles"; }
function addSelectedFiles(inputId, listId, kind) {
  const input = $(inputId);
  const files = [...(input.files || [])];
  if (!files.length) return;
  const key = selectedFilesKey(kind);
  if (kind === "audio") {
    const invalid = files.find((file) => !file.name.toLowerCase().endsWith(".wav") || file.size > 10 * 1024 * 1024);
    if (invalid) return moduleMessage("edit-tts-message", `文件不可用：${invalid.name}（仅支持 10 MB 内 WAV）`, true);
  }
  state[key] = (state[key] || []).concat(files);
  input.value = "";
  renderSelectedChips(listId, kind);
}
function renderSelectedChips(listId, kind) {
  const key = selectedFilesKey(kind);
  const files = state[key] || [];
  const list = $(listId);
  if (!list) return;
  list.classList.toggle("is-hidden", !files.length);
  list.replaceChildren();
  files.forEach((file, index) => {
    const li = document.createElement("li"); li.className = "file-chip";
    const name = document.createElement("b"); name.textContent = file.name;
    const meta = document.createElement("span"); meta.className = "file-chip-meta"; meta.textContent = formatFileSize(file.size);
    const actions = document.createElement("span"); actions.className = "file-chip-actions";
    const preview = document.createElement("button"); preview.type = "button"; preview.title = kind === "audio" ? "试听" : "预览"; preview.setAttribute("aria-label", preview.title); preview.innerHTML = `<i data-lucide="${kind === "audio" ? "play" : "eye"}"></i>`;
    preview.addEventListener("click", () => kind === "audio" ? playSelectedAudio(file) : previewSelectedFile(file));
    actions.append(preview);
    const remove = document.createElement("button"); remove.type = "button"; remove.className = "is-danger"; remove.title = "移除"; remove.setAttribute("aria-label", "移除"); remove.innerHTML = '<i data-lucide="trash-2"></i>';
    remove.addEventListener("click", () => { state[key].splice(index, 1); renderSelectedChips(listId, kind); });
    actions.append(remove);
    li.append(name, meta, actions);
    list.append(li);
  });
  if (window.lucide) window.lucide.createIcons();
}
function setupDropZone(zoneId, inputId, listId, kind) {
  const zone = $(zoneId);
  if (!zone) return;
  zone.addEventListener("click", () => $(inputId).click());
  zone.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); $(inputId).click(); } });
  zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("is-dragging"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("is-dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("is-dragging");
    const files = [...(event.dataTransfer?.files || [])];
    if (!files.length) return;
    const key = selectedFilesKey(kind);
    if (kind === "audio") {
      const invalid = files.find((file) => !file.name.toLowerCase().endsWith(".wav") || file.size > 10 * 1024 * 1024);
      if (invalid) return moduleMessage("edit-tts-message", `文件不可用：${invalid.name}（仅支持 10 MB 内 WAV）`, true);
    }
    state[key] = (state[key] || []).concat(files);
    renderSelectedChips(listId, kind);
  });
}
function setupCreateDropZone() {
  const zone = $("create-files-drop");
  const input = $("document-files");
  if (!zone || !input) return;
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); input.click(); }
  });
  zone.addEventListener("dragover", (event) => { event.preventDefault(); zone.classList.add("is-dragging"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("is-dragging"));
  zone.addEventListener("drop", (event) => {
    event.preventDefault();
    zone.classList.remove("is-dragging");
    const files = [...(event.dataTransfer?.files || [])];
    if (!files.length) return;
    const transfer = new DataTransfer();
    files.forEach((file) => transfer.items.add(file));
    input.files = transfer.files;
    summarizeFiles("document-files", "file-summary", "未选择文件");
  });
}
let selectedAudioPlayer = null;
function playSelectedAudio(file) {
  if (selectedAudioPlayer) { selectedAudioPlayer.pause(); selectedAudioPlayer = null; }
  const url = URL.createObjectURL(file);
  const audio = new Audio(url);
  selectedAudioPlayer = audio;
  audio.onended = () => URL.revokeObjectURL(url);
  audio.onerror = () => URL.revokeObjectURL(url);
  const play = window.PL && window.PL.audio ? window.PL.audio.play(audio) : audio.play();
  play.catch(() => URL.revokeObjectURL(url));
}
function previewSelectedFile(file) {
  $("preview-title").textContent = file.name;
  const openDrawer = () => { $("preview-drawer").classList.add("is-open"); $("preview-backdrop").classList.add("is-open"); };
  if (file.type.startsWith("image/")) {
    const reader = new FileReader();
    reader.onload = () => {
      const img = document.createElement("img"); img.src = reader.result; img.alt = file.name; img.className = "selectable"; img.style.maxWidth = "100%";
      $("preview-content").replaceChildren(img);
      openDrawer();
    };
    reader.readAsDataURL(file);
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    $("preview-content").replaceChildren(document.createTextNode(String(reader.result || "该文件暂不支持预览")));
    openDrawer();
  };
  reader.readAsText(file);
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
  setText("create-status", "分析中");
  const form = new FormData();
  form.append("mode", document.querySelector('input[name="mode"]:checked').value);
  files.forEach((file) => form.append("files", file));
  if (text) form.append("files", new File([text], `text-${Date.now()}.txt`, { type: "text/plain;charset=utf-8" }));
  const submit = $("upload-button"); submit.disabled = true; setText("upload-error");
  setBatchBusy(true); showCreateStep("upload");
  try {
    state.draft = await api(fetch("/api/persona-drafts/upload", { method: "POST", body: form }));
    showCreateStep("analyze");
    await waitForDraftAnalysis();
    state.draft = await api(fetch(`/api/persona-drafts/${state.draft.id}`));
    renderDraft();
    showCreateStep("confirm");
  } catch (reason) { setText("upload-error", reason); setText("create-status", "失败"); showCreateStep(""); }
  finally { submit.disabled = false; setBatchBusy(false); }
}
function showCreateStep(step) {
  const rail = $("create-steps");
  if (!rail) return;
  if (!step) { rail.classList.add("is-hidden"); return; }
  rail.classList.remove("is-hidden");
  const active = CREATE_STEP_ORDER.indexOf(step);
  rail.querySelectorAll("li").forEach((li) => {
    const index = CREATE_STEP_ORDER.indexOf(li.dataset.step);
    li.classList.toggle("is-active", index === active);
    li.classList.toggle("is-complete", index < active);
  });
}
function setBatchBusy(busy) {
  $("batch-form").querySelectorAll("input, textarea").forEach((element) => { element.disabled = busy; });
}
async function waitForDraftAnalysis() {
  $("draft-analyzing").classList.remove("is-hidden");
  try {
    while (state.draft && state.draft.status === "analyzing") {
      await new Promise((resolve) => setTimeout(resolve, 800));
      state.draft = await api(fetch(`/api/persona-drafts/${state.draft.id}`));
    }
  } finally {
    $("draft-analyzing").classList.add("is-hidden");
  }
}
function renderDraft() {
  $("draft-editor").classList.remove("is-hidden");
  $("draft-name").value = state.draft.suggested_name;
  $("draft-profile").value = state.draft.profile?.description || "";
  $("draft-status").textContent = DRAFT_STATUS_LABELS[state.draft.status] || state.draft.status;
  setText("create-status", DRAFT_STATUS_LABELS[state.draft.status] || state.draft.status);
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
function renderDocuments(container, documents, allowRetry = false, allowDelete = false) {
  container.replaceChildren();
  if (!documents.length) return container.append(empty("暂无资料"));
  for (const item of documents) {
    const row = document.createElement("div"); row.className = "document-row";
    const name = document.createElement("span"); name.textContent = item.original_filename;
    const status = DOCUMENT_STATUS_LABELS[item.status] || { label: item.status, tone: "" };
    const badge = document.createElement("span"); badge.className = `document-state${status.tone ? ` is-${status.tone}` : ""}`; badge.textContent = status.label;
    const actions = document.createElement("div"); actions.className = "document-actions";
    const preview = document.createElement("button"); preview.type = "button"; preview.textContent = "预览";
    preview.addEventListener("click", () => openPreview(item)); actions.append(preview);
    if (allowRetry && item.status === "index_failed") {
      const retry = document.createElement("button"); retry.type = "button"; retry.textContent = "重试";
      retry.addEventListener("click", () => retryDocument(item.id)); actions.append(retry);
    }
    if (allowDelete) {
      const del = document.createElement("button"); del.type = "button"; del.className = "is-danger"; del.title = "删除"; del.setAttribute("aria-label", "删除"); del.innerHTML = '<i data-lucide="trash-2"></i>';
      del.addEventListener("click", () => deleteEditDocument(item.id)); actions.append(del);
    }
    if (["converting", "preview_ready", "indexing"].includes(item.status)) {
      row.classList.add("is-pending");
      const progress = document.createElement("span");
      progress.className = "document-progress";
      progress.setAttribute("role", "progressbar");
      row.append(progress);
    }
    row.append(name, badge, actions); container.append(row);
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
    renderDraft();
    await switchView("manage");
    await loadPersonas();
    await selectManagePersona(state.draft.persona.id);
    moduleMessage("edit-files-message", "角色已创建，请配置参考音色后保存");
    pollDraft();
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
function resetDraft() {
  clearTimeout(state.poller);
  state.draft = null;
  $("batch-form").reset();
  setText("create-status", "待开始");
  $("draft-editor").classList.add("is-hidden");
  $("draft-analyzing").classList.add("is-hidden");
  showCreateStep("");
  summarizeFiles("document-files", "file-summary", "未选择文件");
  setText("upload-error");
}
async function loadPersonas(selectId = "") {
  try {
    state.personas = await api(fetch("/api/personas"));
    if ($("persona-list")) renderPersonaList();
    if ($("manage-persona-list")) renderManagePersonaList();
    if (selectId) await selectPersona(selectId);
  } catch (reason) {
    const node = $("chat-error");
    if (node) setText("chat-error", reason);
  }
}
function renderPersonaList() {
  const list = $("persona-list");
  if (!list) return;
  list.replaceChildren();
  for (const persona of state.personas) {
    const button = document.createElement("button"); button.type = "button"; button.className = "persona-item";
    button.classList.toggle("is-active", state.activePersona?.id === persona.id); button.textContent = persona.name;
    button.setAttribute("role", "menuitem");
    button.addEventListener("click", () => selectPersona(persona.id));
    $("persona-list").append(button);
  }
}
function renderManagePersonaList() {
  const list = $("manage-persona-list");
  if (!list) return;
  const count = $("manage-count");
  if (count) count.textContent = `${state.personas.length} 个角色`;
  list.replaceChildren();
  if (!state.personas.length) {
    const empty = document.createElement("p");
    empty.className = "manage-empty";
    empty.textContent = "还没有角色，去「新建」页创建第一个角色吧";
    list.append(empty);
    return;
  }
  for (const persona of state.personas) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "manage-persona-card";
    button.classList.toggle("is-selected", state.manageSelectedId === persona.id);
    const name = document.createElement("b");
    name.textContent = persona.name;
    const description = document.createElement("span");
    description.textContent = (persona.profile?.description || "暂无设定描述").slice(0, 60);
    button.append(name, description);
    button.addEventListener("click", () => selectManagePersona(persona.id));
    list.append(button);
  }
}
async function selectManagePersona(personaId) {
  state.manageSelectedId = personaId;
  renderManagePersonaList();
  await loadEditPersona(personaId);
  const workspace = $("edit-persona-workspace");
  if (workspace && !workspace.classList.contains("is-hidden")) {
    workspace.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}
async function loadEditPersona(personaId = null) {
  clearTimeout(state.editPoller);
  try {
    const id = personaId ?? state.manageSelectedId;
    state.editPersona = id ? await api(fetch(`/api/personas/${id}`)) : null;
    $("edit-persona-workspace").classList.toggle("is-hidden", !state.editPersona);
    $("delete-persona").disabled = !state.editPersona;
    if (!state.editPersona) return;
    state.editSelectedFiles = [];
    state.editSelectedAudio = [];
    $("edit-document-files").value = "";
    $("edit-tts-reference").value = "";
    $("edit-direct-text").value = "";
    renderSelectedChips("edit-files-selected", "files");
    renderSelectedChips("edit-tts-selected", "audio");
    moduleMessage("edit-files-message", "");
    moduleMessage("edit-live2d-message", "");
    moduleMessage("edit-tts-message", "");
    $("edit-persona-name").value = state.editPersona.name;
    $("edit-persona-profile").value = state.editPersona.profile?.description || "";
    $("edit-tts-enabled").checked = Boolean(state.editPersona.profile?.tts?.enabled);
    $("edit-tts-auto-play").checked = state.editPersona.profile?.tts?.auto_play !== false;
    await syncEditLive2dModel();
    await loadEditReference();
    syncEditTtsControls();
    await loadEditDocuments();
    await loadEditMCPGrants();
  } catch (reason) {
    moduleMessage("edit-files-message", reason, true);
  }
}

async function loadEditMCPGrants() {
  const personaId = state.editPersona?.id;
  const list = $("edit-mcp-grant-list");
  const status = $("edit-mcp-grants-status");
  const message = $("edit-mcp-grants-message");
  if (!personaId) return;
  list.innerHTML = "";
  let data;
  try {
    data = await api(fetch(`/api/personas/${encodeURIComponent(personaId)}/mcp-grants`));
  } catch (reason) {
    message.textContent = reason.message || reason;
    message.classList.add("is-error");
    return;
  }
  const servers = data.servers || [];
  status.textContent = `${servers.filter((s) => s.authorized).length} / ${servers.length} 台`;
  if (!servers.length) {
    list.append(empty("暂无 MCP 服务器，请到插件页配置后再授权。"));
    $("edit-mcp-grants-save").disabled = true;
    return;
  }
  $("edit-mcp-grants-save").disabled = false;
  for (const server of servers) {
    const label = document.createElement("label");
    label.className = "toggle-field skill-tool-option";
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = server.name;
    checkbox.checked = Boolean(server.authorized);
    const span = document.createElement("span");
    span.textContent = `${server.name}${server.enabled ? "" : "（已停用）"}`;
    label.append(checkbox, span);
    list.append(label);
  }
}

async function saveEditMCPGrants() {
  const personaId = state.editPersona?.id;
  const message = $("edit-mcp-grants-message");
  if (!personaId) return;
  const serverNames = Array.from(
    $("edit-mcp-grant-list").querySelectorAll("input:checked")
  ).map((input) => input.value);
  try {
    await api(fetch(`/api/personas/${encodeURIComponent(personaId)}/mcp-grants`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ server_names: serverNames }),
    }));
    message.textContent = "授权已保存。";
    message.classList.remove("is-error");
    await loadEditMCPGrants();
  } catch (reason) {
    message.textContent = reason.message || reason;
    message.classList.add("is-error");
  }
}
let live2dModelOptions = null;
async function loadLive2dModelOptions() {
  if (live2dModelOptions) return live2dModelOptions;
  try {
    const data = await api(fetch("/api/live2d/models"));
    live2dModelOptions = (data && data.models) || [];
  } catch (e) {
    live2dModelOptions = [];
  }
  return live2dModelOptions;
}
async function syncEditLive2dModel() {
  const select = $("edit-live2d-model");
  const status = $("edit-live2d-status");
  if (!select) return;
  const models = await loadLive2dModelOptions();
  const bound = state.editPersona?.profile?.live2d?.model || "";
  select.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "默认（不绑定）";
  select.append(empty);
  for (const model of models) {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = model.id;
    select.append(option);
  }
  select.value = bound;
  if (status) {
    const matched = models.some((m) => m.id === bound);
    status.textContent = bound && matched ? `已绑定：${bound}` : "未绑定";
  }
}
async function loadEditReference() {
  if (!state.editPersona) return;
  const info = await api(fetch(`/api/tts/personas/${state.editPersona.id}/reference`, { headers: { "X-YUMENO-Request": "web" } }));
  const duration = info.duration_seconds ? ` · 约 ${Math.round(info.duration_seconds)} 秒` : "";
  setText("edit-tts-reference-status", info.configured ? `已配置：${info.name}${info.count > 1 ? `（已合并 ${info.count} 条${duration}）` : duration}` : "未配置参考音色");
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
async function playEditReference() {
  if (!state.editPersona) return;
  try {
    const response = await fetch(`/api/tts/personas/${state.editPersona.id}/reference/audio`, { headers: { "X-YUMENO-Request": "web" } });
    if (!response.ok) throw new Error("参考音色尚未配置");
    if (state.editReferenceUrl) URL.revokeObjectURL(state.editReferenceUrl);
    state.editReferenceUrl = URL.createObjectURL(await response.blob());
    if (window.PL && window.PL.unlockAudio) window.PL.unlockAudio();
    const audio = new Audio(state.editReferenceUrl);
    const play = window.PL && window.PL.audio ? window.PL.audio.play(audio) : audio.play();
    play.catch(() => {});
  } catch (reason) { moduleMessage("edit-tts-message", reason, true); }
}
async function removeEditReference() {
  if (!state.editPersona || !window.confirm("移除当前角色的参考音色？")) return;
  try {
    await api(fetch(`/api/tts/personas/${state.editPersona.id}/reference`, { method: "DELETE", headers: { "X-YUMENO-Request": "web" } }));
    state.editPersona.profile = { ...(state.editPersona.profile || {}), tts: { ...(state.editPersona.profile?.tts || {}) } };
    delete state.editPersona.profile.tts.reference_audio;
    moduleMessage("edit-tts-message", "参考音色已移除"); await loadEditReference();
  } catch (reason) { moduleMessage("edit-tts-message", reason, true); }
}
async function generateEditPreview() {
  if (!state.editPersona) return;
  if (!state.ttsConfigured) return openTtsSettings();
  const text = $("edit-tts-preview-text").value.trim();
  if (!text) return setText("edit-tts-preview-status", "请输入示例文案");
  const button = $("edit-tts-generate-preview"); button.disabled = true; setText("edit-tts-preview-status", "正在生成示例语音…");
  try {
    const response = await fetch(`/api/tts/personas/${state.editPersona.id}/reference/preview`, { method: "POST", headers: { "Content-Type": "application/json", "X-YUMENO-Request": "web" }, body: JSON.stringify({ text }) });
    if (!response.ok) { const data = await response.json().catch(() => null); if (response.status === 409) { setText("edit-tts-preview-status", "请先安装 TTS 模型"); return openTtsSettings(); } throw new Error(data?.detail || `请求失败 (${response.status})`); }
    const audio = $("edit-tts-preview-audio"); if (audio.src) URL.revokeObjectURL(audio.src); audio.src = URL.createObjectURL(await response.blob()); audio.classList.remove("is-hidden"); setText("edit-tts-preview-status", "示例语音已生成");
    if (window.PL && window.PL.unlockAudio) window.PL.unlockAudio();
    audio.play().catch((error) => {
      if (error && error.name === "NotAllowedError") {
        setText("edit-tts-preview-status", "示例语音已生成，点击播放按钮播放");
      }
    });
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
    state.manageSelectedId = null;
    $("edit-persona-workspace").classList.add("is-hidden");
    $("delete-persona").disabled = true;
    const nextPersona = state.personas.find((item) => item.id !== persona.id) || null;
    state.activePersona = null;
    await loadPersonas(nextPersona?.id || "");
    if (!nextPersona) selectPersona();
  } catch (reason) { setText("delete-persona-error", reason); }
  finally {
    $("delete-persona-confirm").disabled = false;
    $("delete-persona-cancel").disabled = false;
  }
}
async function loadEditDocuments() {
  if (!state.editPersona) return;
  const documents = await api(fetch(`/api/personas/${state.editPersona.id}/documents`));
  renderDocuments($("edit-document-list"), documents, false, true);
  const busy = documents.some((item) => ["converting", "preview_ready", "indexing"].includes(item.status));
  const message = $("edit-files-message");
  if (!busy && message && message.textContent === "资料已保存，正在写入 Milvus 向量库…") moduleMessage("edit-files-message", "资料已保存");
  if (busy) state.editPoller = setTimeout(loadEditDocuments, 1200);
}
async function saveEditFiles(fromAll = false) {
  if (!state.editPersona) return false;
  const name = $("edit-persona-name").value.trim();
  if (!name) { moduleMessage("edit-files-message", "请填写角色名称", true); return false; }
  const confirm = $("edit-files-confirm");
  if (!fromAll) confirm.disabled = true;
  moduleMessage("edit-files-message", "正在保存资料…");
  try {
    const profile = { ...(state.editPersona.profile || {}), description: $("edit-persona-profile").value.trim() };
    state.editPersona = await api(fetch(`/api/personas/${state.editPersona.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, profile }) }));
    await loadPersonas();
    state.manageSelectedId = state.editPersona.id;
    renderManagePersonaList();
    const files = state.editSelectedFiles || [];
    const text = $("edit-direct-text").value.trim();
    if (files.length || text) {
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      if (text) form.append("files", new File([text], `text-${Date.now()}.txt`, { type: "text/plain;charset=utf-8" }));
      const jobs = await api(fetch(`/api/knowledge-spaces/${state.editPersona.knowledge_space_id}/documents/upload`, { method: "POST", body: form }));
      await Promise.all(jobs.map((job) => api(fetch(`/api/documents/${job.id}/confirm`, { method: "POST" }))));
      state.editSelectedFiles = [];
      $("edit-direct-text").value = "";
      renderSelectedChips("edit-files-selected", "files");
      moduleMessage("edit-files-message", "资料已保存，正在写入 Milvus 向量库…");
    } else {
      moduleMessage("edit-files-message", "资料已保存");
    }
    await loadEditDocuments();
    return true;
  } catch (reason) { moduleMessage("edit-files-message", reason, true); return false; }
  finally { if (!fromAll) confirm.disabled = false; }
}
async function saveEditLive2d(fromAll = false) {
  if (!state.editPersona) return false;
  const confirm = $("edit-live2d-confirm");
  if (!fromAll) confirm.disabled = true;
  moduleMessage("edit-live2d-message", "正在保存形象…");
  try {
    const profile = { ...(state.editPersona.profile || {}), live2d: { model: $("edit-live2d-model")?.value || "" } };
    state.editPersona = await api(fetch(`/api/personas/${state.editPersona.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile }) }));
    syncEditLive2dModel();
    moduleMessage("edit-live2d-message", "形象已保存");
    return true;
  } catch (reason) { moduleMessage("edit-live2d-message", reason, true); return false; }
  finally { if (!fromAll) confirm.disabled = false; }
}
async function saveEditVoice(fromAll = false) {
  if (!state.editPersona) return false;
  const confirm = $("edit-tts-confirm");
  if (!fromAll) confirm.disabled = true;
  try {
    const pending = state.editSelectedAudio || [];
    if (pending.length) {
      moduleMessage("edit-tts-message", `正在上传 ${pending.length} 条参考音频…`);
      const form = new FormData();
      pending.forEach((file) => form.append("files", file));
      state.editPersona = await api(fetch(`/api/tts/personas/${state.editPersona.id}/reference`, { method: "POST", headers: { "X-YUMENO-Request": "web" }, body: form }));
      state.editSelectedAudio = [];
      renderSelectedChips("edit-tts-selected", "audio");
    }
    const profile = { ...(state.editPersona.profile || {}), tts: { ...(state.editPersona.profile?.tts || {}), enabled: $("edit-tts-enabled").checked, auto_play: $("edit-tts-auto-play").checked } };
    state.editPersona = await api(fetch(`/api/personas/${state.editPersona.id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ profile }) }));
    await loadEditReference();
    moduleMessage("edit-tts-message", "声音设置已保存");
    return true;
  } catch (reason) { moduleMessage("edit-tts-message", reason, true); return false; }
  finally { if (!fromAll) confirm.disabled = false; }
}
async function deleteEditDocument(documentId) {
  if (!confirm("从角色资料中删除该文件？知识库向量与本地文件将一并移除。")) return;
  try {
    await api(fetch(`/api/documents/${documentId}`, { method: "DELETE" }));
    await loadEditDocuments();
    moduleMessage("edit-files-message", "资料已删除");
  } catch (reason) { moduleMessage("edit-files-message", reason, true); }
}
function requestSaveAll() {
  if (!state.editPersona) return;
  const pendingFiles = (state.editSelectedFiles || []).length + ($("edit-direct-text").value.trim() ? 1 : 0);
  const pendingAudio = (state.editSelectedAudio || []).length;
  const summary = [
    `名称：${$("edit-persona-name").value.trim() || "（未填写）"}`,
    `人设：${$("edit-persona-profile").value.trim().slice(0, 80) || "（未填写）"}`,
    `Live2D：${$("edit-live2d-model")?.value || "默认（不绑定）"}`,
    `语音：${$("edit-tts-enabled").checked ? "生成语音" : "关闭"}${$("edit-tts-auto-play").checked ? " · 自动播放" : ""}`,
    `资料：${pendingFiles ? `${pendingFiles} 个待上传` : "无新增"}`,
    `参考音色：${pendingAudio ? `${pendingAudio} 条待上传` : "无新增"}`,
  ].join("\n");
  setText("save-all-detail", summary);
  setText("save-all-error");
  $("save-all-dialog").showModal();
}
async function confirmSaveAll() {
  const confirm = $("save-all-confirm"); confirm.disabled = true;
  try {
    const ok = await saveEditFiles(true) && await saveEditLive2d(true) && await saveEditVoice(true);
    if (ok) {
      $("save-all-dialog").close();
      moduleMessage("edit-files-message", "全部修改已保存");
    }
  } catch (reason) { setText("save-all-error", reason); }
  finally { confirm.disabled = false; }
}

async function loadEvalPersonas() {
  const list = await api(fetch("/api/personas"));
  const select = $("eval-persona");
  select.innerHTML = '<option value="">请选择角色</option>' + list
    .map((persona) => `<option value="${persona.id}">${persona.name}</option>`)
    .join("");
}

const EVAL_METRIC_LABELS = {
  recall_at_k_answerable: "可答问题召回率 recall@k",
  precision_at_k_answerable: "可答问题精确率 precision@k",
  mrr_answerable: "可答问题 MRR",
  hit_at_1_answerable: "可答问题首位命中 hit@1",
  cases_answerable: "可答用例数",
  mean_latency_ms: "平均检索延迟 (ms)",
  p95_latency_ms: "P95 检索延迟 (ms)",
  grounded_rate: "事实接地率 grounded",
  useful_rate: "问题解决率 useful",
  cases_checked: "生成已检用例",
  cases_total: "用例总数",
  refusal_rate: "拒答率",
  answer_rate: "正常作答率",
  accepted_rate: "通过质量门率",
  mean_confidence: "平均置信度",
  rewrite_rate: "查询改写触发率",
  correction_rate: "生成纠错触发率",
  mean_rewrite_count: "平均改写次数",
  mean_correction_count: "平均纠错次数",
  cases_complex: "复杂题数",
  complex_rewrite_rate: "复杂题改写率",
  complex_correction_rate: "复杂题纠错率",
  probe_refusal_rate: "无关问题拒答率",
  mean_total_latency_ms: "平均整链路延迟 (ms)",
  p95_total_latency_ms: "P95 整链路延迟 (ms)",
  scope_isolation_ok: "跨角色隔离校验",
};

const EVAL_PERCENT_KEYS = new Set([
  "recall_at_k_answerable",
  "precision_at_k_answerable",
  "mrr_answerable",
  "hit_at_1_answerable",
  "grounded_rate",
  "useful_rate",
  "refusal_rate",
  "answer_rate",
  "accepted_rate",
  "rewrite_rate",
  "correction_rate",
  "complex_rewrite_rate",
  "complex_correction_rate",
  "probe_refusal_rate",
  "mean_confidence",
]);

function renderEvalMetrics(metrics) {
  metrics = metrics || {};
  const rows = Object.entries(EVAL_METRIC_LABELS)
    .filter(([key]) => metrics[key] !== undefined && metrics[key] !== null)
    .map(([key, label]) => {
      const number = Number(metrics[key]);
      let value;
      let tone = "";
      if (EVAL_PERCENT_KEYS.has(key) && Number.isFinite(number)) {
        value = `${Math.round(number * 100)}%`;
        tone = number >= 0.8 ? "is-good" : number <= 0.5 ? "is-bad" : "";
      } else if (key === "scope_isolation_ok") {
        value = metrics[key] ? "通过" : "未通过";
        tone = metrics[key] ? "is-good" : "is-bad";
      } else if (typeof metrics[key] === "number" && Number.isFinite(number)) {
        value = Number.isInteger(number) ? String(number) : number.toFixed(3);
      } else {
        value = String(metrics[key]);
      }
      return `<div class="eval-metric"><span>${label}</span><b${tone ? ` class="${tone}"` : ""}>${value}</b></div>`;
    });
  $("eval-metrics").innerHTML = `<div class="eval-metric-grid">${rows.join("")}</div>`;
  $("eval-metrics").classList.remove("is-hidden");
}

function renderEvalCases(cases) {
  cases = cases || [];
  const list = cases.map((caseItem, index) => {
    const answer = (caseItem.answer || "").slice(0, 120);
    const verdict = caseItem.is_probe
      ? (caseItem.refused ? ["符合预期", "is-ok"] : ["未通过", "is-bad"])
      : (caseItem.grounded === null || caseItem.grounded === undefined)
        ? ["待判定", ""]
        : (caseItem.accepted ? ["符合预期", "is-ok"] : ["未通过", "is-bad"]);
    const boolFlag = (name, value) =>
      value === null || value === undefined
        ? `${name}=—`
        : `<span class="${value ? "flag-ok" : "flag-bad"}">${name}=${value}</span>`;
    const flags = [
      boolFlag("grounded", caseItem.grounded),
      boolFlag("useful", caseItem.useful),
      `confidence=${caseItem.confidence ?? "—"}`,
      caseItem.refused ? `<span class="${caseItem.is_probe ? "flag-ok" : "flag-bad"}">拒答</span>` : "",
      caseItem.rewrite_used ? "查询改写" : "",
      caseItem.corrected ? "生成纠错" : "",
      caseItem.is_complex ? "复杂题" : "",
      caseItem.is_probe ? "无关探针" : "",
    ].filter(Boolean).join(" · ");
    return `<div class="eval-case ${verdict[1]}"><div class="eval-case-head"><b>${index + 1}. ${caseItem.question}</b><span class="eval-verdict ${verdict[1]}">${verdict[0]}</span></div><p>${answer}</p><span class="eval-flags">${flags}</span></div>`;
  });
  $("eval-cases").innerHTML = list.join("");
  $("eval-details").classList.remove("is-hidden");
}

async function pollEvalResult() {
  const autoButton = $("eval-auto-run");
  const analyzeButton = $("eval-analyze");
  autoButton.disabled = true;
  analyzeButton.disabled = true;
  autoButton.textContent = "生成中…";
  $("eval-analysis").classList.add("is-hidden");
  const progress = $("eval-progress");
  progress.classList.remove("is-hidden");
  for (let i = 0; i < 1200; i += 1) {
    const status = await api(fetch("/api/eval/status"));
    $("eval-state").textContent = status.state === "running" ? "评测中" : status.state;
    $("eval-state-pill").textContent = status.state === "running" ? "进行中" : status.state;
    if (status.phase === "generating") {
      $("eval-status").textContent = status.status_text || "正在从角色资料生成问题…";
      $("eval-state").textContent = "生成中";
      $("eval-state-pill").textContent = "生成中";
      progress.removeAttribute("value");
    } else if (status.total > 0) {
      progress.value = Math.round((status.progress / status.total) * 100);
      const parts = [`已完成 ${status.progress}/${status.total} 条`];
      if (status.current_question) parts.push(status.current_question);
      if (status.current_step) parts.push(`环节：${status.current_step}`);
      if (status.current_question_text) parts.push(`问题：${status.current_question_text}`);
      $("eval-status").textContent = parts.join(" · ");
    }
    if (status.state === "done") {
      const panel = $("eval-panel");
      if (panel) panel.open = true;
      try {
        const results = await api(fetch("/api/eval/results"));
        renderEvalMetrics(results.metrics);
        renderEvalCases(results.cases);
        $("eval-status").textContent = "评测完成";
      } catch (error) {
        $("eval-status").textContent = `结果加载失败：${error.message || error}`;
      }
      $("eval-state-pill").textContent = "已完成";
      analyzeButton.disabled = false;
      const metricsNode = $("eval-metrics");
      if (metricsNode && !metricsNode.classList.contains("is-hidden")) {
        metricsNode.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
      break;
    }
    if (status.state === "error") {
      $("eval-status").textContent = status.error || "评测失败";
      $("eval-state-pill").textContent = "失败";
      break;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  progress.classList.add("is-hidden");
  autoButton.disabled = false;
  autoButton.textContent = "一键生成并评测";
}

function bindEvalEvents() {
  const startEval = () => {
    const personaId = $("eval-persona").value;
    if (!personaId) {
      $("eval-status").textContent = "请先选择评测角色";
      return;
    }
    const tier = $("eval-tier").value;
    $("eval-status").textContent = "";
    $("eval-metrics").classList.add("is-hidden");
    $("eval-details").classList.add("is-hidden");
    $("eval-analysis").classList.add("is-hidden");
    api(fetch("/api/eval/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ persona_id: personaId, tier }),
    }))
      .then(() => pollEvalResult())
      .catch((reason) => {
        $("eval-status").textContent = reason.message || reason;
        $("eval-state-pill").textContent = "失败";
      });
  };
  const analyze = async () => {
    const button = $("eval-analyze");
    button.disabled = true;
    button.textContent = "分析中…";
    const block = $("eval-analysis");
    block.classList.remove("is-hidden");
    block.textContent = "AI 正在分析评测结果…";
    try {
      const result = await api(fetch("/api/eval/analyze", { method: "POST" }));
      block.textContent = result.analysis;
    } catch (reason) {
      block.textContent = reason.message || reason;
    } finally {
      button.disabled = false;
      button.textContent = "AI 分析";
    }
  };
  bindSafe("eval-auto-run", "click", startEval);
  bindSafe("eval-analyze", "click", analyze);
}
