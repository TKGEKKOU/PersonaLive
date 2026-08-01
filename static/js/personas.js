"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.upload = { init: initPersonas };

function initPersonas() {
  bindPersonasEvents();
  fillPersonaSelect($("edit-persona-select"), "请选择角色");
}

function bindPersonasEvents() {
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
  $("delete-persona").addEventListener("click", requestPersonaDeletion);
  $("delete-persona-cancel").addEventListener("click", () => $("delete-persona-dialog").close());
  $("delete-persona-confirm").addEventListener("click", confirmPersonaDeletion);
  $("close-preview").addEventListener("click", closePreview);
  $("preview-backdrop").addEventListener("click", closePreview);
}
function switchMaterialMode(mode) {
  $("create-material-panel").classList.toggle("is-hidden", mode !== "create");
  $("edit-material-panel").classList.toggle("is-hidden", mode !== "edit");
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
