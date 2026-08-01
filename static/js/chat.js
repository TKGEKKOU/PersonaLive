"use strict";
window.PL = window.PL || { modules: {} };
window.PL.modules.chat = { init: initChat };

let chatGlobalEventsBound = false;

function bindChatGlobalEvents() {
  if (chatGlobalEventsBound) return;
  chatGlobalEventsBound = true;
  document.addEventListener("click", (event) => { if (!event.target.closest(".chat-persona-picker")) closePersonaMenu(); });
  document.addEventListener("click", (event) => { if (!event.target.closest(".chat-settings")) closeChatSettingsMenu(); });
}

function initChat() {
  bindChatEvents();
  bindChatGlobalEvents();
  renderPersonaList();
}

function bindChatEvents() {
  $("question-form").addEventListener("submit", submitQuestion);
  $("chat-process-toggle").addEventListener("click", toggleChatProcess);
  $("question").addEventListener("input", resizeComposer);
  $("cancel-generation").addEventListener("click", cancelRealtimeTurn);
  $("record-audio").addEventListener("click", () => state.audioMode === "recording" ? finishAudioRecording() : startAudioRecording());
  $("cancel-audio").addEventListener("click", cancelAudioActivity);
  $("confirm-action").addEventListener("click", () => resumeAgent(true));
  $("cancel-action").addEventListener("click", () => resumeAgent(false));
  $("question").addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!$("send-question").disabled) $("question-form").requestSubmit();
    }
  });
  $("clear-conversation").addEventListener("click", clearConversation);
  $("chat-persona-toggle").addEventListener("click", togglePersonaDrawer);
  $("chat-settings-toggle").addEventListener("click", (event) => { event.stopPropagation(); toggleChatSettingsMenu(); });
  document.querySelectorAll("#chat-settings-menu button").forEach((button) => button.addEventListener("click", closeChatSettingsMenu));
  $("assistant-voice-toggle").addEventListener("change", () => localStorage.setItem("personalive:assistant-voice", $("assistant-voice-toggle").checked ? "on" : "off"));
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
